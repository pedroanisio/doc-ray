"""DB-backed tests for the projection engine (SLP Block D, #8).

Exercises the real schema (migration 129) against a throwaway Postgres, driving the
refresh through :mod:`doc_ray.application.projection`. Pins the block's binding
acceptance criteria (issue §8): fiction isolation, non-fiction sharing, lens overlap,
explicit global fallback, reproducibility of a rebuild, substrate immutability,
incremental-vs-full agreement, cascade-on-reannotate consistency (with a durable
review re-applied by span), RLS, and the FR-2 designation rules. Skips when no safe
test DB is reachable.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

_itdb = pytest.importorskip("_itdb")

from doc_ray.application import projection as ap  # noqa: E402
from doc_ray.domain.ulid import new_ulid_str  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]
_ORG_CORPUS_MIGRATION = _ROOT / "db" / "migrations" / "110_org_corpus.sql"


@pytest.fixture
def conn():
    c = _itdb.connect()
    try:
        yield c
    finally:
        c.rollback()
        c.close()


# --- substrate builders ---------------------------------------------------


def _canonical(conn: Any, preferred: str, entity_type: str, wikidata_id: str | None = None) -> int:
    return conn.execute(
        "INSERT INTO canonical (preferred, entity_type, wikidata_id) VALUES (%s, %s, %s) "
        "RETURNING id",
        (preferred, entity_type, wikidata_id),
    ).fetchone()["id"]


def _annotate(conn: Any, doc_id: str, mentions: list[tuple[int, int, str, str]]) -> list[str]:
    """Create an annotation + a mention per (char_start, char_end, entity_type, surface).

    Returns the new mention ids in the given order.
    """
    ann = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s,%s,'spacy','test')",
        (ann, doc_id),
    )
    ids: list[str] = []
    for start, end, etype, surface in mentions:
        span = conn.execute(
            "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,%s,%s) "
            "ON CONFLICT (document_id, char_start, char_end) DO UPDATE SET char_start = %s "
            "RETURNING id",
            (doc_id, start, end, start),
        ).fetchone()["id"]
        mid = new_ulid_str()
        conn.execute(
            "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
            "VALUES (%s,%s,%s,%s,%s)",
            (mid, ann, span, etype, surface),
        )
        ids.append(mid)
    return ids


def _link_global(conn: Any, mention_id: str, canonical_id: int, confidence: float = 1.0) -> None:
    conn.execute(
        "INSERT INTO entity_link (mention_id, scheme, kb_id, confidence) "
        "VALUES (%s, 'canonical', %s, %s) ON CONFLICT (mention_id, scheme) DO UPDATE "
        "SET kb_id = EXCLUDED.kb_id",
        (mention_id, str(canonical_id), confidence),
    )


def _lens(conn: Any, name: str, *, is_scope: bool = True, projects: bool = True) -> str:
    return str(
        conn.execute(
            "INSERT INTO document_collections (name, kind, is_scope, projects) "
            "VALUES (%s, 'manual', %s, %s) RETURNING id",
            (f"{name}-{uuid.uuid4().hex[:8]}", is_scope, projects),
        ).fetchone()["id"]
    )


def _add_member(conn: Any, lens_id: str, doc_id: str) -> None:
    conn.execute(
        "INSERT INTO document_collection_members (collection_id, document_id) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING",
        (lens_id, doc_id),
    )


def _set_genre(conn: Any, doc_id: str, genre: str, status: str = "pending") -> None:
    conn.execute(
        "INSERT INTO document_classification (document_id, genre, method, status) "
        "VALUES (%s, %s, 'heuristic', %s) ON CONFLICT (document_id) DO UPDATE "
        "SET genre = EXCLUDED.genre, status = EXCLUDED.status",
        (doc_id, genre, status),
    )


def _projections(conn: Any, doc_id: str, lens_id: str | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT mention_id, lens_id, canonical_id, scope, method, status "
        "FROM entity_projection WHERE document_id = %s"
    )
    params: tuple[Any, ...] = (doc_id,)
    if lens_id is not None:
        sql += " AND lens_id = %s"
        params = (doc_id, lens_id)
    return conn.execute(sql + " ORDER BY mention_id, lens_id", params).fetchall()


def _global_lens_id(conn: Any) -> str:
    return str(
        conn.execute("SELECT id FROM document_collections WHERE name = 'global'").fetchone()["id"]
    )


# --- headline acceptance criteria -----------------------------------------


def test_fiction_isolation_same_surface_resolves_to_different_local_ids(conn):
    """Two fictional works with the same surface 'Duncan' get DIFFERENT identities."""
    lens_a, lens_b = _lens(conn, "dune"), _lens(conn, "macbeth")
    doc_a, doc_b = (
        _itdb.make_doc(conn, "Duncan in Dune."),
        _itdb.make_doc(conn, "Duncan in Scotland."),
    )
    for doc, lens in ((doc_a, lens_a), (doc_b, lens_b)):
        _add_member(conn, lens, doc)
        _set_genre(conn, doc, "fiction")
        _annotate(conn, doc, [(0, 6, "PERSON", "Duncan")])
        ap.refresh_projections_for_document(conn, doc)

    a = _projections(conn, doc_a, lens_a)
    b = _projections(conn, doc_b, lens_b)
    assert len(a) == 1 and a[0]["scope"] == "local" and a[0]["canonical_id"] is not None
    assert len(b) == 1 and b[0]["scope"] == "local" and b[0]["canonical_id"] is not None
    assert a[0]["canonical_id"] != b[0]["canonical_id"], "fictional Duncans must not merge"


def test_non_fiction_sharing_same_surface_shares_one_global_id(conn):
    """The same real 'Paris' in two histories resolves to the SAME canonical id."""
    paris = _canonical(conn, "Paris", "GPE", wikidata_id=f"Q{uuid.uuid4().hex[:12]}")
    doc_a, doc_b = (
        _itdb.make_doc(conn, "Paris history A."),
        _itdb.make_doc(conn, "Paris history B."),
    )
    for doc in (doc_a, doc_b):
        _set_genre(conn, doc, "non_fiction")
        [mid] = _annotate(conn, doc, [(0, 5, "GPE", "Paris")])
        _link_global(conn, mid, paris)
        ap.refresh_projections_for_document(conn, doc)

    gl = _global_lens_id(conn)
    a = _projections(conn, doc_a, gl)
    b = _projections(conn, doc_b, gl)
    assert a[0]["scope"] == "global" and a[0]["canonical_id"] == paris
    assert b[0]["canonical_id"] == paris, "the same real place must be shared"


def test_overlap_one_document_two_lenses_one_projection_each(conn):
    """A document in two projecting lenses gets one projection per lens; they differ."""
    lens_a, lens_b = _lens(conn, "legendarium"), _lens(conn, "films")
    doc = _itdb.make_doc(conn, "Gandalf appears.")
    _add_member(conn, lens_a, doc)
    _add_member(conn, lens_b, doc)
    _set_genre(conn, doc, "fiction")
    [mid] = _annotate(conn, doc, [(0, 7, "PERSON", "Gandalf")])
    ap.refresh_projections_for_document(conn, doc)

    rows = _projections(conn, doc)
    by_lens = {r["lens_id"]: r for r in rows}
    assert len(rows) == 3, "global + two scope lenses"
    assert by_lens[uuid.UUID(lens_a)]["scope"] == "local"
    assert by_lens[uuid.UUID(lens_b)]["scope"] == "local"
    # The two lenses minted independent local identities.
    assert by_lens[uuid.UUID(lens_a)]["canonical_id"] != by_lens[uuid.UUID(lens_b)]["canonical_id"]
    # The global lens row is unresolved (no global link) — present, not dropped.
    assert by_lens[uuid.UUID(_global_lens_id(conn))]["scope"] == "global"


def test_global_fallback_unmatched_is_explicitly_unresolved_never_dropped(conn):
    doc = _itdb.make_doc(conn, "Nobody in particular.")
    _set_genre(conn, doc, "non_fiction")
    _annotate(conn, doc, [(0, 6, "GPE", "Nowhere")])  # no global link
    ap.refresh_projections_for_document(conn, doc)

    gl = _global_lens_id(conn)
    rows = _projections(conn, doc, gl)
    assert len(rows) == 1, "the mention was not silently dropped"
    assert rows[0]["canonical_id"] is None
    assert rows[0]["scope"] == "global" and rows[0]["method"] == "unresolved"


def test_all_machine_projections_are_proposed(conn):
    doc = _itdb.make_doc(conn, "Duncan again.")
    lens = _lens(conn, "work")
    _add_member(conn, lens, doc)
    _set_genre(conn, doc, "fiction")
    _annotate(conn, doc, [(0, 6, "PERSON", "Duncan")])
    ap.refresh_projections_for_document(conn, doc)
    assert all(r["status"] == "proposed" for r in _projections(conn, doc))


# --- reproducibility / immutability ---------------------------------------


def _semantic(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (str(r["mention_id"]), str(r["lens_id"]), r["canonical_id"], r["scope"], r["method"])
        for r in rows
    ]


def test_truncate_and_rebuild_yields_identical_rows(conn):
    lens = _lens(conn, "work")
    doc = _itdb.make_doc(conn, "Paul and Duncan.")
    _add_member(conn, lens, doc)
    _set_genre(conn, doc, "fiction")
    _annotate(conn, doc, [(0, 4, "PERSON", "Paul"), (9, 15, "PERSON", "Duncan")])
    ap.refresh_projections_for_document(conn, doc)
    before = _semantic(_projections(conn, doc))

    conn.execute("DELETE FROM entity_projection WHERE document_id = %s", (doc,))
    ap.refresh_projections_for_document(conn, doc)
    after = _semantic(_projections(conn, doc))
    assert before == after and len(before) == 4  # 2 mentions × (global + 1 scope lens)


def test_substrate_is_never_mutated_by_projection(conn):
    real = _canonical(conn, "Paris", "GPE", wikidata_id=f"Q{uuid.uuid4().hex[:12]}")
    doc = _itdb.make_doc(conn, "Duncan in Paris.")
    lens = _lens(conn, "work")
    _add_member(conn, lens, doc)
    _set_genre(conn, doc, "fiction")
    ids = _annotate(conn, doc, [(0, 6, "PERSON", "Duncan"), (10, 15, "GPE", "Paris")])
    _link_global(conn, ids[1], real)

    def em_digest() -> list[tuple[Any, ...]]:
        return conn.execute(
            "SELECT id, entity_type, surface, span_id FROM entity_mention ORDER BY id"
        ).fetchall()

    def canon_digest() -> list[tuple[Any, ...]]:
        return conn.execute(
            "SELECT id, preferred, entity_type, wikidata_id FROM canonical ORDER BY id"
        ).fetchall()

    em_before, canon_before = em_digest(), canon_digest()
    ap.refresh_projections_for_document(conn, doc)
    # entity_mention is untouched; existing canonical rows are untouched (new local-only
    # rows may be APPENDED, but nothing pre-existing is deleted or rewritten).
    assert em_digest() == em_before
    assert all(row in canon_digest() for row in canon_before)
    assert (
        conn.execute("SELECT preferred FROM canonical WHERE id=%s", (real,)).fetchone()["preferred"]
        == "Paris"
    )


def test_incremental_and_full_rebuild_agree(conn):
    lens = _lens(conn, "work")
    doc = _itdb.make_doc(conn, "Duncan story.")
    _add_member(conn, lens, doc)
    _set_genre(conn, doc, "fiction")
    _annotate(conn, doc, [(0, 6, "PERSON", "Duncan")])
    ap.refresh_projections_for_document(conn, doc)
    incremental = _semantic(_projections(conn, doc))

    ap.rebuild_all_projections(conn)
    full = _semantic(_projections(conn, doc))
    assert incremental == full


# --- re-annotation cascade + durable review by span -----------------------


def test_reannotate_cascades_then_rebuilds_and_reapplies_review_by_span(conn):
    lens = _lens(conn, "work")
    doc = _itdb.make_doc(conn, "Duncan lives here.")
    _add_member(conn, lens, doc)
    _set_genre(conn, doc, "fiction")
    _annotate(conn, doc, [(0, 6, "PERSON", "Duncan")])
    ap.refresh_projections_for_document(conn, doc)
    assert len(_projections(conn, doc)) == 2  # global + scope

    # A human confirms the scope-lens projection, keyed by the STABLE span.
    conn.execute(
        "INSERT INTO entity_projection_review "
        "(document_id, lens_id, char_start, char_end, entity_type, decision) "
        "VALUES (%s, %s, 0, 6, 'PERSON', 'confirmed')",
        (doc, lens),
    )

    # Re-annotate: deleting the annotation cascades entity_mention -> entity_projection.
    conn.execute("DELETE FROM annotations WHERE document_id = %s", (doc,))
    assert _projections(conn, doc) == [], "projections cascaded away with their mentions"

    # Fresh mentions get NEW ids at the SAME span; refresh rebuilds and re-applies review.
    _annotate(conn, doc, [(0, 6, "PERSON", "Duncan")])
    ap.refresh_projections_for_document(conn, doc)
    scope_rows = _projections(conn, doc, lens)
    assert len(scope_rows) == 1
    assert scope_rows[0]["status"] == "confirmed", "review survived re-annotation via span key"


# --- FR-2 designation + fan-out bound -------------------------------------


def test_projects_requires_scope_check(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO document_collections (name, kind, is_scope, projects) "
            "VALUES (%s, 'manual', false, true)",
            (f"bad-{uuid.uuid4().hex[:8]}",),
        )


def test_max_projecting_lenses_bound_is_enforced_and_reported(conn):
    doc = _itdb.make_doc(conn, "Crowded document.")
    _set_genre(conn, doc, "fiction")
    _annotate(conn, doc, [(0, 6, "PERSON", "Duncan")])
    # global + (MAX) scope lenses => one over the bound.
    for i in range(ap.MAX_PROJECTING_LENSES_PER_DOCUMENT):
        _add_member(conn, _lens(conn, f"lens{i}"), doc)
    result = ap.refresh_projections_for_document(conn, doc)
    assert result["lenses_projected"] == ap.MAX_PROJECTING_LENSES_PER_DOCUMENT
    assert result["lenses_dropped_over_bound"] >= 1


def test_non_projecting_scope_lens_is_not_materialized(conn):
    lens = _lens(conn, "quiet", is_scope=True, projects=False)  # a lens, but not designated
    doc = _itdb.make_doc(conn, "Duncan quietly.")
    _add_member(conn, lens, doc)
    _set_genre(conn, doc, "fiction")
    _annotate(conn, doc, [(0, 6, "PERSON", "Duncan")])
    ap.refresh_projections_for_document(conn, doc)
    assert _projections(conn, doc, lens) == [], "an undesignated lens does not project"
    # …but the global lens still does.
    assert len(_projections(conn, doc, _global_lens_id(conn))) == 1


# --- lens entity rollup (lensEntities read model) -------------------------


def test_lens_entity_stats_rollup_counts_local_entities(conn):
    lens = _lens(conn, "saga")
    doc = _itdb.make_doc(conn, "Duncan, Duncan, and Paul.")
    _add_member(conn, lens, doc)
    _set_genre(conn, doc, "fiction")
    _annotate(
        conn,
        doc,
        [(0, 6, "PERSON", "Duncan"), (8, 14, "PERSON", "Duncan"), (20, 24, "PERSON", "Paul")],
    )
    ap.refresh_projections_for_document(conn, doc)
    stats = conn.execute(
        "SELECT canonical_id, n_mentions, n_documents FROM lens_entity_stats "
        "WHERE lens_id = %s ORDER BY n_mentions DESC",
        (lens,),
    ).fetchall()
    assert stats[0]["n_mentions"] == 2 and stats[0]["n_documents"] == 1  # the two Duncans merged
    assert {int(s["n_mentions"]) for s in stats} == {2, 1}


# --- RLS ------------------------------------------------------------------


def _make_member(conn: Any, name: str) -> str:
    pid = str(
        conn.execute(
            "INSERT INTO principals (kind, display_name, tenant_id) "
            "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
            (name,),
        ).fetchone()["id"]
    )
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) "
        "SELECT %s, id FROM roles WHERE name = 'member' ON CONFLICT DO NOTHING",
        (pid,),
    )
    return pid


async def _read_projection_count_as(principal_id: str, document_id: str) -> int:
    aconn = await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )
    try:
        await aconn.execute("SET ROLE docray_api")
        await aconn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
        cur = await aconn.execute(
            "SELECT count(*) AS n FROM entity_projection WHERE document_id = %s", (document_id,)
        )
        row = await cur.fetchone()
        return int(row["n"])
    finally:
        await aconn.execute("SELECT set_config('app.principal_id', '', false)")
        await aconn.execute("RESET ROLE")
        await aconn.close()


def test_rls_no_cross_principal_projection_read(conn):
    import asyncio

    conn.execute(_ORG_CORPUS_MIGRATION.read_text(encoding="utf-8"))
    alice = _make_member(conn, f"alice-{uuid.uuid4()}")
    bob = _make_member(conn, f"bob-{uuid.uuid4()}")
    doc = _itdb.make_doc(conn, "Alice's Duncan.")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (alice, doc))
    _set_genre(conn, doc, "non_fiction")
    _annotate(conn, doc, [(0, 6, "PERSON", "Duncan")])
    ap.refresh_projections_for_document(conn, doc)
    try:
        assert asyncio.run(_read_projection_count_as(alice, doc)) >= 1
        assert asyncio.run(_read_projection_count_as(bob, doc)) == 0, "Bob must not see it"
    finally:
        conn.execute("UPDATE documents SET owner_principal_id = NULL WHERE id = %s", (doc,))
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (alice, bob))
