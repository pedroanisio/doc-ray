"""DB-backed tests for the hyper-context lens rollups (issue #10).

Exercises the real schema (migration 133) against a throwaway Postgres. Pins the binding
acceptance criteria: rollup counts + type profile against hand-computed expectations,
the NPMI strength (incl. the ubiquitous-entity degenerate case), undirected-pair
canonicalisation queried from both directions, incremental-vs-full agreement +
idempotence, RLS (no cross-principal rollup reads), and graceful degradation without
allusion edges. Skips when no safe test DB is reachable.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

_itdb = pytest.importorskip("_itdb")

from doc_ray.adapters import lens_rollup_store  # noqa: E402
from doc_ray.adapters import persistence_lens_rollup_read as rollup_read  # noqa: E402
from doc_ray.application import lens_rollups as app_rollups  # noqa: E402
from doc_ray.application import projection as ap  # noqa: E402
from doc_ray.domain.lens_rollups import npmi  # noqa: E402
from doc_ray.domain.ulid import new_ulid_str  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]
_ORG = _ROOT / "db" / "migrations" / "110_org_corpus.sql"


@pytest.fixture
def conn():
    c = _itdb.connect()
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _lens(conn: Any, name: str) -> str:
    return str(
        conn.execute(
            "INSERT INTO document_collections (name, kind, is_scope, projects) "
            "VALUES (%s, 'manual', true, true) RETURNING id",
            (f"{name}-{uuid.uuid4().hex[:8]}",),
        ).fetchone()["id"]
    )


def _sentence(conn: Any, doc: str, ordinal: int, cstart: int, cend: int) -> None:
    span = conn.execute(
        "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,%s,%s) RETURNING id",
        (doc, cstart, cend),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO sentences (id, document_id, ordinal, text, span_id) VALUES (%s,%s,%s,%s,%s)",
        (new_ulid_str(), doc, ordinal, f"sentence {ordinal}", span),
    )


def _mention(conn: Any, doc: str, ann: str, cstart: int, cend: int, surface: str) -> None:
    span = conn.execute(
        "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,%s,%s) RETURNING id",
        (doc, cstart, cend),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
        "VALUES (%s,%s,%s,'PERSON',%s)",
        (new_ulid_str(), ann, span, surface),
    )


def _fiction_lens_with_cooccurrence(conn: Any) -> tuple[str, str]:
    """A fiction doc: 4 sentences, entities Alpha(×3) Beta(×2) Gamma(×1) Delta Epsilon.

    s0:[0,10) Alpha,Beta ; s1:[10,20) Alpha,Beta ; s2:[20,30) Alpha,Gamma ; s3:[30,40) Delta,Epsilon.
    So co-occur: (Alpha,Beta)=2, (Alpha,Gamma)=1, (Delta,Epsilon)=1; 4 sentences total.
    """
    lens = _lens(conn, "saga")
    doc = _itdb.make_doc(conn, "seed")
    conn.execute(
        "DELETE FROM sentences WHERE document_id = %s", (doc,)
    )  # controlled sentences only
    conn.execute(
        "INSERT INTO document_collection_members (collection_id, document_id) VALUES (%s,%s)",
        (lens, doc),
    )
    conn.execute(
        "INSERT INTO document_classification (document_id, genre, method, status) "
        "VALUES (%s,'fiction','heuristic','pending')",
        (doc,),
    )
    for ordv, (a, b) in enumerate([(0, 10), (10, 20), (20, 30), (30, 40)]):
        _sentence(conn, doc, ordv, a, b)
    ann = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s,%s,'spacy','test')",
        (ann, doc),
    )
    placements = [
        (0, 1, "Alpha"),
        (2, 3, "Beta"),
        (10, 11, "Alpha"),
        (12, 13, "Beta"),
        (20, 21, "Alpha"),
        (22, 23, "Gamma"),
        (30, 31, "Delta"),
        (32, 33, "Epsilon"),
    ]
    for cstart, cend, surface in placements:
        _mention(conn, doc, ann, cstart, cend, surface)
    ap.refresh_projections_for_document(conn, doc)  # projects + CHAINS the rollup refresh
    return lens, doc


def _rollup_by_name(conn: Any, lens: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT c.preferred, r.mention_count, r.document_count, r.type_profile, "
        "       r.type_consistency, r.first_seen_ordinal "
        "FROM lens_entity_rollup r JOIN canonical c ON c.id = r.canonical_id WHERE r.lens_id = %s",
        (lens,),
    ).fetchall()
    return {r["preferred"]: r for r in rows}


# --- rollup counts + type profile (hand-computed) --------------------------


def test_entity_rollup_counts_and_type_profile(conn):
    lens, _doc = _fiction_lens_with_cooccurrence(conn)
    by = _rollup_by_name(conn, lens)
    assert by["Alpha"]["mention_count"] == 3 and by["Alpha"]["document_count"] == 1
    assert by["Beta"]["mention_count"] == 2
    assert by["Gamma"]["mention_count"] == 1
    assert by["Alpha"]["type_profile"] == {"PERSON": 3}
    assert by["Alpha"]["type_consistency"] == pytest.approx(1.0)
    assert by["Alpha"]["first_seen_ordinal"] == 0  # first appears in sentence 0


# --- relations: NPMI + undirected pair queried both directions -------------


def _relation_strength(conn: Any, lens: str, name_a: str, name_b: str) -> float | None:
    row = conn.execute(
        """
        SELECT r.strength FROM lens_entity_relation r
        JOIN canonical ca ON ca.id = r.from_canonical_id
        JOIN canonical cb ON cb.id = r.to_canonical_id
        WHERE r.lens_id = %s AND ((ca.preferred=%s AND cb.preferred=%s)
                                  OR (ca.preferred=%s AND cb.preferred=%s))
        """,
        (lens, name_a, name_b, name_b, name_a),
    ).fetchone()
    return row["strength"] if row else None


def test_relation_strength_matches_npmi(conn):
    lens, _doc = _fiction_lens_with_cooccurrence(conn)
    # (Alpha,Beta): joint=2, df_A=3, df_B=2, total=4.  (Delta,Epsilon): joint=1, df=1,1, total=4 → 1.0.
    assert _relation_strength(conn, lens, "Alpha", "Beta") == pytest.approx(
        npmi(2, 3, 2, 4), rel=1e-5
    )
    assert _relation_strength(conn, lens, "Delta", "Epsilon") == pytest.approx(1.0, rel=1e-5)
    # Beta and Gamma never share a sentence → no stored relation.
    assert _relation_strength(conn, lens, "Beta", "Gamma") is None


def test_relation_pair_stored_once_from_less_than_to(conn):
    lens, _doc = _fiction_lens_with_cooccurrence(conn)
    rows = conn.execute(
        "SELECT from_canonical_id, to_canonical_id FROM lens_entity_relation WHERE lens_id = %s",
        (lens,),
    ).fetchall()
    assert all(r["from_canonical_id"] < r["to_canonical_id"] for r in rows)


def test_entity_relations_read_finds_relations_from_either_endpoint(conn):
    lens, _doc = _fiction_lens_with_cooccurrence(conn)
    alpha = conn.execute(
        "SELECT r.canonical_id FROM lens_entity_rollup r JOIN canonical c ON c.id=r.canonical_id "
        "WHERE r.lens_id=%s AND c.preferred='Alpha'",
        (lens,),
    ).fetchone()["canonical_id"]

    async def go():
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            rels = await rollup_read.entity_relations(aconn, lens, alpha)
            names = {r["preferred"] for r in rels}
            assert names == {"Beta", "Gamma"}  # Alpha relates to both, via either stored ordering
            assert all("refreshed_at" in r for r in rels)  # staleness stamp exposed
        finally:
            await aconn.close()

    asyncio.run(go())


# --- bounded N-hop read -----------------------------------------------------


def test_related_entities_bounded_hop_and_rejects_over_cap(conn):
    lens, _doc = _fiction_lens_with_cooccurrence(conn)
    alpha = conn.execute(
        "SELECT r.canonical_id FROM lens_entity_rollup r JOIN canonical c ON c.id=r.canonical_id "
        "WHERE r.lens_id=%s AND c.preferred='Alpha'",
        (lens,),
    ).fetchone()["canonical_id"]

    async def go():
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            reached = await rollup_read.related_entities(aconn, lens, alpha, hops=2)
            names = {r["preferred"] for r in reached}
            # Alpha—Beta, Alpha—Gamma (1 hop). Delta/Epsilon are a disjoint component → unreachable.
            assert names == {"Beta", "Gamma"}
            assert all(r["hops"] == 1 for r in reached)
            with pytest.raises(ValueError, match="hops"):
                await rollup_read.related_entities(aconn, lens, alpha, hops=99)
        finally:
            await aconn.close()

    asyncio.run(go())


# --- incremental == full, idempotence ---------------------------------------


def _snapshot(conn: Any, lens: str) -> tuple[Any, Any]:
    r = conn.execute(
        "SELECT canonical_id, mention_count, document_count, type_consistency, first_seen_ordinal "
        "FROM lens_entity_rollup WHERE lens_id=%s ORDER BY canonical_id",
        (lens,),
    ).fetchall()
    rel = conn.execute(
        "SELECT from_canonical_id, to_canonical_id, cooccurrence_count, strength "
        "FROM lens_entity_relation WHERE lens_id=%s ORDER BY from_canonical_id, to_canonical_id",
        (lens,),
    ).fetchall()
    return (r, rel)


def test_refresh_is_idempotent_and_full_matches_incremental(conn):
    lens, _doc = _fiction_lens_with_cooccurrence(conn)  # chained (incremental) refresh already ran
    incremental = _snapshot(conn, lens)
    lens_rollup_store.refresh_lens_rollup(conn, lens)  # run again
    assert _snapshot(conn, lens) == incremental  # idempotent
    app_rollups.rebuild_all_lens_rollups(conn)  # full rebuild across all projecting lenses
    assert _snapshot(conn, lens) == incremental  # full == incremental


# --- degrade gracefully without allusion edges (allusion edges) --------------------


def test_rollups_work_without_any_allusion_edges(conn):
    # No entity_allusion rows created here; the rollups are intra-lens and must not error.
    lens, _doc = _fiction_lens_with_cooccurrence(conn)
    assert (
        conn.execute(
            "SELECT count(*) n FROM lens_entity_rollup WHERE lens_id=%s", (lens,)
        ).fetchone()["n"]
        == 5
    )


# --- RLS: no cross-principal rollup reads -----------------------------------


def _member(conn: Any, name: str) -> str:
    pid = str(
        conn.execute(
            "INSERT INTO principals (kind, display_name, tenant_id) "
            "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
            (name,),
        ).fetchone()["id"]
    )
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) "
        "SELECT %s, id FROM roles WHERE name='member' ON CONFLICT DO NOTHING",
        (pid,),
    )
    return pid


async def _count_as(principal_id: str, lens_id: str) -> int:
    aconn = await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )
    try:
        await aconn.execute("SET ROLE docray_api")
        await aconn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
        cur = await aconn.execute(
            "SELECT count(*) AS n FROM lens_entity_rollup WHERE lens_id = %s", (lens_id,)
        )
        return int((await cur.fetchone())["n"])
    finally:
        await aconn.execute("SELECT set_config('app.principal_id', '', false)")
        await aconn.execute("RESET ROLE")
        await aconn.close()


def test_rls_no_cross_principal_rollup_read(conn):
    conn.execute(_ORG.read_text(encoding="utf-8"))
    alice = _member(conn, f"alice-{uuid.uuid4()}")
    bob = _member(conn, f"bob-{uuid.uuid4()}")
    lens, doc = _fiction_lens_with_cooccurrence(conn)
    conn.execute("UPDATE documents SET owner_principal_id=%s WHERE id=%s", (alice, doc))
    try:
        assert asyncio.run(_count_as(alice, lens)) == 5, "alice owns the lens's doc → sees rollup"
        assert asyncio.run(_count_as(bob, lens)) == 0, "bob sees no doc in the lens → no rollup"
    finally:
        conn.execute("UPDATE documents SET owner_principal_id=NULL WHERE id=%s", (doc,))
        conn.execute("DELETE FROM principals WHERE id IN (%s,%s)", (alice, bob))
