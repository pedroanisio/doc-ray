"""Behaviour of the incremental tenant rollups (migration 154).

Before 154 both tenant rollups were rebuilt from the whole corpus on a
ten-minute debounce, so any drift was erased within one cycle and none of these
properties had to hold. With deltas they are the correctness contract: the
rollup is only ever nudged, never re-derived, so a miscounted re-annotation or
an unwithdrawn document delete is permanent until someone runs a reconciler.

Runs against the live schema, like every other DB-backed suite here (skips
cleanly without the isolated test database).
"""

from __future__ import annotations

from pathlib import Path

from doc_ray.adapters import persistence as db
from doc_ray.domain.ulid import new_ulid_str

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATIONS = (
    "095_corpus_stats.sql",
    "098_reader_stats.sql",
    "100_read_model_token_entity.sql",
    "152_corpus_stats_total_lexeme_uses.sql",
    "154_incremental_corpus_rollups.sql",
)


def _apply(conn) -> None:
    for name in _MIGRATIONS:
        conn.execute((_ROOT / "db" / "migrations" / name).read_text())


def _tenant_of(conn, doc_id: str) -> str:
    row = conn.execute("SELECT tenant_id FROM documents WHERE id = %s", (doc_id,)).fetchone()
    return str(row["tenant_id"])


def _uses(conn, tenant: str, lexeme_uid: str) -> int | None:
    row = conn.execute(
        "SELECT uses FROM lexeme_stats WHERE tenant_id = %s AND lexeme_uid = %s",
        (tenant, lexeme_uid),
    ).fetchone()
    return None if row is None else int(row["uses"])


def _entity_uses(conn, tenant: str, surface: str) -> int | None:
    row = conn.execute(
        "SELECT uses FROM entity_frequency_stats "
        "WHERE tenant_id = %s AND entity_text = %s AND entity_type = 'PERSON'",
        (tenant, surface),
    ).fetchone()
    return None if row is None else int(row["uses"])


def _seed_lexeme_doc(conn, occurrences: int) -> tuple[str, str, str]:
    """A document whose text carries `occurrences` uses of one private lexeme.

    Returns (doc_id, tenant_id, lexeme_uid). The lemma is unique per call so a
    tenant-wide rollup shared with every other document in the test database
    still yields an exact expected count.
    """
    import _itdb

    lemma = f"zyzzo{_itdb.unique_sha()[:12]}"
    text = " ".join([lemma] * max(occurrences, 1)) + "."
    doc_id = _itdb.make_doc(conn, text)
    annotation_id = _itdb.make_annotation(conn, doc_id, "lexeme", "test")
    lexeme_uid, _ = db.get_or_create_lexeme(conn, {}, lemma, "NOUN", "en")
    _add_occurrences(conn, doc_id, annotation_id, lexeme_uid, occurrences)
    return doc_id, _tenant_of(conn, doc_id), lexeme_uid


def _add_occurrences(conn, doc_id: str, annotation_id: str, lexeme_uid: str, n: int) -> None:
    width = 12
    ranges = [(i * width, i * width + width - 1) for i in range(n)]
    if not ranges:
        return
    spans = db.ensure_spans(conn, doc_id, ranges)
    for r in ranges:
        conn.execute(
            "INSERT INTO lexeme_occurrences (annotation_id, span_id, lexeme_uid) "
            "VALUES (%s, %s, %s)",
            (annotation_id, spans[r], lexeme_uid),
        )


def _refresh(conn, doc_id: str) -> None:
    conn.execute("SELECT docray_refresh_stats_for_document(%s)", (doc_id,))


# ---------------------------------------------------------------- lexemes ---


def test_incremental_refresh_matches_the_live_count(pg) -> None:
    conn, created = pg
    _apply(conn)
    doc_id, tenant, lexeme_uid = _seed_lexeme_doc(conn, 3)
    created.append(doc_id)

    _refresh(conn, doc_id)

    assert _uses(conn, tenant, lexeme_uid) == 3


def test_incremental_refresh_agrees_with_the_full_reconciler(pg) -> None:
    """The delta path and the rebuild path must not disagree — 154's whole premise."""
    conn, created = pg
    _apply(conn)
    doc_id, tenant, _lexeme_uid = _seed_lexeme_doc(conn, 4)
    created.append(doc_id)

    _refresh(conn, doc_id)
    incremental = conn.execute(
        "SELECT lexeme_uid, uses FROM lexeme_stats WHERE tenant_id = %s ORDER BY lexeme_uid",
        (tenant,),
    ).fetchall()

    conn.execute("SELECT docray_refresh_lexeme_stats(%s)", (tenant,))
    rebuilt = conn.execute(
        "SELECT lexeme_uid, uses FROM lexeme_stats WHERE tenant_id = %s ORDER BY lexeme_uid",
        (tenant,),
    ).fetchall()

    assert [dict(r) for r in incremental] == [dict(r) for r in rebuilt]


def test_reannotation_replaces_rather_than_accumulates(pg) -> None:
    """The failure a delta rollup invites: adding the new count without removing the old."""
    conn, created = pg
    _apply(conn)
    doc_id, tenant, lexeme_uid = _seed_lexeme_doc(conn, 3)
    created.append(doc_id)
    _refresh(conn, doc_id)
    assert _uses(conn, tenant, lexeme_uid) == 3

    conn.execute(
        "DELETE FROM lexeme_occurrences o USING annotations a "
        "WHERE a.id = o.annotation_id AND a.document_id = %s",
        (doc_id,),
    )
    annotation_id = _itdb_make_annotation(conn, doc_id)
    _add_occurrences(conn, doc_id, annotation_id, lexeme_uid, 1)

    _refresh(conn, doc_id)

    assert _uses(conn, tenant, lexeme_uid) == 1


def _itdb_make_annotation(conn, doc_id: str) -> str:
    import _itdb

    return _itdb.make_annotation(conn, doc_id, "lexeme", "test")


def test_repeated_refresh_is_idempotent(pg) -> None:
    """A no-op refresh must apply a zero delta, not re-add the document's counts."""
    conn, created = pg
    _apply(conn)
    doc_id, tenant, lexeme_uid = _seed_lexeme_doc(conn, 2)
    created.append(doc_id)

    for _ in range(3):
        _refresh(conn, doc_id)

    assert _uses(conn, tenant, lexeme_uid) == 2


def test_exhausted_lexeme_is_pruned(pg) -> None:
    """A key that falls to zero must leave, or n_distinct_lexemes counts dead keys."""
    conn, created = pg
    _apply(conn)
    doc_id, tenant, lexeme_uid = _seed_lexeme_doc(conn, 2)
    created.append(doc_id)
    _refresh(conn, doc_id)
    assert _uses(conn, tenant, lexeme_uid) == 2

    conn.execute(
        "DELETE FROM lexeme_occurrences o USING annotations a "
        "WHERE a.id = o.annotation_id AND a.document_id = %s",
        (doc_id,),
    )
    _refresh(conn, doc_id)

    assert _uses(conn, tenant, lexeme_uid) is None


def test_deleting_a_document_withdraws_its_contribution(pg) -> None:
    """Nothing withdrew a deleted document before 154 — the next rebuild hid it."""
    conn, _created = pg
    _apply(conn)
    doc_id, tenant, lexeme_uid = _seed_lexeme_doc(conn, 5)
    _refresh(conn, doc_id)
    assert _uses(conn, tenant, lexeme_uid) == 5

    conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))

    assert _uses(conn, tenant, lexeme_uid) is None
    leftover = conn.execute(
        "SELECT count(*) AS n FROM document_lexeme_stats WHERE document_id = %s", (doc_id,)
    ).fetchone()
    assert leftover["n"] == 0


def test_two_documents_sharing_a_lexeme_sum_and_survive_one_delete(pg) -> None:
    conn, created = pg
    _apply(conn)
    first, tenant, lexeme_uid = _seed_lexeme_doc(conn, 2)
    created.append(first)

    import _itdb

    second = _itdb.make_doc(conn, "shared lexeme document.")
    annotation_id = _itdb.make_annotation(conn, second, "lexeme", "test")
    _add_occurrences(conn, second, annotation_id, lexeme_uid, 3)

    _refresh(conn, first)
    _refresh(conn, second)
    assert _uses(conn, tenant, lexeme_uid) == 5

    conn.execute("DELETE FROM documents WHERE id = %s", (second,))

    assert _uses(conn, tenant, lexeme_uid) == 2


# ----------------------------------------------------------- corpus_stats ---


def test_corpus_stats_derive_from_the_rollup_it_summarises(pg) -> None:
    """154 moved both scalars off lexeme_occurrences and onto lexeme_stats."""
    conn, created = pg
    _apply(conn)
    doc_id, tenant, _lexeme_uid = _seed_lexeme_doc(conn, 3)
    created.append(doc_id)

    _refresh(conn, doc_id)

    truth = conn.execute(
        "SELECT count(*) AS n_distinct, COALESCE(SUM(uses), 0) AS total "
        "FROM lexeme_stats WHERE tenant_id = %s",
        (tenant,),
    ).fetchone()
    stats = conn.execute(
        "SELECT n_distinct_lexemes, total_lexeme_uses FROM corpus_stats WHERE tenant_id = %s",
        (tenant,),
    ).fetchone()

    assert stats["n_distinct_lexemes"] == truth["n_distinct"]
    assert stats["total_lexeme_uses"] == truth["total"]


def test_corpus_stats_are_stamped_after_the_rollups(pg) -> None:
    """Pre-154 ordering stamped total_lexeme_uses from the previous cycle."""
    conn, created = pg
    _apply(conn)
    doc_id, tenant, lexeme_uid = _seed_lexeme_doc(conn, 2)
    created.append(doc_id)
    _refresh(conn, doc_id)
    before = conn.execute(
        "SELECT total_lexeme_uses FROM corpus_stats WHERE tenant_id = %s", (tenant,)
    ).fetchone()["total_lexeme_uses"]

    annotation_id = _itdb_make_annotation(conn, doc_id)
    _add_occurrences(conn, doc_id, annotation_id, lexeme_uid, 4)
    _refresh(conn, doc_id)

    after = conn.execute(
        "SELECT total_lexeme_uses FROM corpus_stats WHERE tenant_id = %s", (tenant,)
    ).fetchone()["total_lexeme_uses"]
    assert after == before + 4, "the same refresh that applied the delta must publish it"


def test_refresh_is_not_debounced(pg) -> None:
    """The ten-minute window amortised a rebuild that no longer happens."""
    conn, created = pg
    _apply(conn)
    doc_id, tenant, lexeme_uid = _seed_lexeme_doc(conn, 1)
    created.append(doc_id)
    _refresh(conn, doc_id)

    annotation_id = _itdb_make_annotation(conn, doc_id)
    _add_occurrences(conn, doc_id, annotation_id, lexeme_uid, 2)
    # Default corpus_max_age; pre-154 this returned without touching anything
    # because corpus_stats.computed_at was seconds old.
    _refresh(conn, doc_id)

    assert _uses(conn, tenant, lexeme_uid) == 3


# ------------------------------------------------------- entity frequency ---


def test_entity_frequency_tracks_mention_changes(pg) -> None:
    conn, created = pg
    _apply(conn)
    import _itdb

    surface = f"Zyzz {_itdb.unique_sha()[:10]}"
    doc_id = _itdb.make_doc(conn, f"{surface} appears twice: {surface}.")
    created.append(doc_id)
    tenant = _tenant_of(conn, doc_id)
    annotation_id = _itdb.make_annotation(conn, doc_id, "ner", "test")
    spans = db.ensure_spans(conn, doc_id, [(0, 10), (30, 40)])
    for rng in ((0, 10), (30, 40)):
        conn.execute(
            "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
            "VALUES (%s, %s, %s, 'PERSON', %s)",
            (new_ulid_str(), annotation_id, spans[rng], surface),
        )

    _refresh(conn, doc_id)
    assert _entity_uses(conn, tenant, surface) == 2

    conn.execute(
        "DELETE FROM entity_mention em USING annotations a "
        "WHERE a.id = em.annotation_id AND a.document_id = %s",
        (doc_id,),
    )
    _refresh(conn, doc_id)

    assert _entity_uses(conn, tenant, surface) is None
