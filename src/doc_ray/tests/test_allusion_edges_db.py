"""DB-backed tests for allusion edges (SLP Block E, #9).

Exercises the real schema (migration 131) against a throwaway Postgres. Pins the
binding acceptance criteria: CRUD + bidirectional read, ``UNIQUE`` idempotence, the
database CHECKs (self-edge, edge_type, confidence), the **substrate-immutability
invariant** (an edge changes nothing about projection/canonical — the test that
protects invariant 2), and the **load-bearing RLS asymmetry** (a caller who can see
only one of the two lenses sees no edge). Skips when no safe test DB is reachable.
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

from doc_ray.adapters import allusion_store  # noqa: E402
from doc_ray.adapters import persistence_allusion_read as allusion_read  # noqa: E402
from doc_ray.application import projection as ap  # noqa: E402
from doc_ray.domain.allusions import Direction  # noqa: E402
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


# --- builders --------------------------------------------------------------


def _canonical(
    conn: Any, preferred: str, etype: str = "GPE", wikidata_id: str | None = None
) -> int:
    return conn.execute(
        "INSERT INTO canonical (preferred, entity_type, wikidata_id) VALUES (%s,%s,%s) RETURNING id",
        (preferred, etype, wikidata_id),
    ).fetchone()["id"]


def _lens(conn: Any, name: str) -> str:
    return str(
        conn.execute(
            "INSERT INTO document_collections (name, kind, is_scope, projects) "
            "VALUES (%s, 'manual', true, true) RETURNING id",
            (f"{name}-{uuid.uuid4().hex[:8]}",),
        ).fetchone()["id"]
    )


def _global_lens(conn: Any) -> str:
    return str(
        conn.execute("SELECT id FROM document_collections WHERE name='global'").fetchone()["id"]
    )


def _insert_edge(
    conn: Any,
    *,
    from_c: int,
    from_l: str,
    to_c: int,
    to_l: str,
    edge_type: str = "alludes_to",
    method: str = "human",
    evidence: str | None = None,
    status: str = "proposed",
) -> str:
    return str(
        conn.execute(
            "INSERT INTO entity_allusion "
            "(from_canonical_id, from_lens_id, to_canonical_id, to_lens_id, edge_type, "
            " method, status, evidence_document_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (from_c, from_l, to_c, to_l, edge_type, method, status, evidence),
        ).fetchone()["id"]
    )


# --- CHECK constraints (enforced by the database) --------------------------


def test_self_edge_within_one_lens_is_rejected(conn):
    lens = _lens(conn, "work")
    c = _canonical(conn, "Duncan", "PERSON")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_edge(conn, from_c=c, from_l=lens, to_c=c, to_l=lens)


def test_same_canonical_across_different_lenses_is_allowed(conn):
    a, b = _lens(conn, "a"), _lens(conn, "b")
    c = _canonical(conn, "Paris", "GPE")
    eid = _insert_edge(conn, from_c=c, from_l=a, to_c=c, to_l=b)  # not a self-edge
    assert eid


def test_unknown_edge_type_is_rejected(conn):
    a, b = _lens(conn, "a"), _lens(conn, "b")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_edge(
            conn,
            from_c=_canonical(conn, "x"),
            from_l=a,
            to_c=_canonical(conn, "y"),
            to_l=b,
            edge_type="is_the_same_as",
        )


def test_confidence_out_of_range_is_rejected(conn):
    a, b = _lens(conn, "a"), _lens(conn, "b")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO entity_allusion "
            "(from_canonical_id, from_lens_id, to_canonical_id, to_lens_id, edge_type, "
            " confidence, method) VALUES (%s,%s,%s,%s,'alludes_to',1.5,'human')",
            (_canonical(conn, "x"), a, _canonical(conn, "y"), b),
        )


# --- idempotence + bidirectional read (via the async store/adapter) --------


def _seed_cross_lens(conn: Any) -> tuple[str, int, int]:
    """A fiction lens with a local Paris that alludes to the global real Paris."""
    fiction = _lens(conn, "novel")
    local_paris = _canonical(conn, "Paris", "GPE")  # work-local (no wikidata)
    real_paris = _canonical(conn, "Paris", "GPE", wikidata_id=f"Q{uuid.uuid4().hex[:10]}")
    return fiction, local_paris, real_paris


def test_propose_is_idempotent(conn):
    fiction, local_paris, real_paris = _seed_cross_lens(conn)
    gl = _global_lens(conn)

    async def go():
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            kw = dict(
                from_canonical_id=local_paris,
                from_lens_id=fiction,
                to_canonical_id=real_paris,
                to_lens_id=gl,
                edge_type="alludes_to",
                method="human",
                confidence=None,
                rationale=None,
                evidence_document_id=None,
            )
            row1, created1 = await allusion_store.propose_allusion(aconn, **kw)
            row2, created2 = await allusion_store.propose_allusion(aconn, **kw)
            assert created1 is True and created2 is False
            assert row1 is not None and row2 is not None
            assert row1["id"] == row2["id"]  # same row, no duplicate, no error
        finally:
            await aconn.close()

    asyncio.run(go())
    n = conn.execute(
        "SELECT count(*) AS n FROM entity_allusion WHERE from_canonical_id=%s AND to_canonical_id=%s",
        (local_paris, real_paris),
    ).fetchone()["n"]
    assert n == 1


def test_bidirectional_read_returns_correct_direction(conn):
    fiction, local_paris, real_paris = _seed_cross_lens(conn)
    gl = _global_lens(conn)
    _insert_edge(conn, from_c=local_paris, from_l=fiction, to_c=real_paris, to_l=gl)

    async def go():
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            # From the fiction Paris: OUTGOING finds the edge; INCOMING does not.
            out = await allusion_read.list_allusions(
                aconn, canonical_id=local_paris, lens_id=fiction, direction=Direction.OUTGOING
            )
            inc = await allusion_read.list_allusions(
                aconn, canonical_id=local_paris, lens_id=fiction, direction=Direction.INCOMING
            )
            assert len(out) == 1 and out[0]["to_preferred"] == "Paris"
            assert out[0]["edge_type"] == "alludes_to"
            assert len(inc) == 0
            # From the real Paris (global): INCOMING finds it; OUTGOING does not.
            inc_real = await allusion_read.list_allusions(
                aconn, canonical_id=real_paris, lens_id=gl, direction=Direction.INCOMING
            )
            assert len(inc_real) == 1 and inc_real[0]["from_lens_name"].startswith("novel")
        finally:
            await aconn.close()

    asyncio.run(go())


# --- invariant 2: an edge changes NOTHING about projection/canonical --------


def test_edge_does_not_mutate_the_substrate(conn):
    fiction = _lens(conn, "novel")
    doc = _itdb.make_doc(conn, "Paris in a novel.")
    conn.execute(
        "INSERT INTO document_collection_members (collection_id, document_id) VALUES (%s,%s)",
        (fiction, doc),
    )
    conn.execute(
        "INSERT INTO document_classification (document_id, genre, method, status) "
        "VALUES (%s,'fiction','heuristic','pending')",
        (doc,),
    )
    ann = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s,%s,'spacy','test')",
        (ann, doc),
    )
    span = conn.execute(
        "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,0,5) RETURNING id", (doc,)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
        "VALUES (%s,%s,%s,'GPE','Paris')",
        (new_ulid_str(), ann, span),
    )
    ap.refresh_projections_for_document(conn, doc)

    def snap() -> tuple[Any, ...]:
        proj = conn.execute(
            "SELECT mention_id, lens_id, canonical_id, scope, status FROM entity_projection "
            "WHERE document_id=%s ORDER BY mention_id, lens_id",
            (doc,),
        ).fetchall()
        canon = conn.execute(
            "SELECT id, preferred, entity_type, wikidata_id FROM canonical ORDER BY id"
        ).fetchall()
        return (proj, canon)

    before = snap()
    # Draw an allusion edge from the local Paris to a real Paris.
    local_paris = conn.execute(
        "SELECT canonical_id FROM entity_projection WHERE document_id=%s AND scope='local' LIMIT 1",
        (doc,),
    ).fetchone()
    real = _canonical(conn, "Paris", "GPE", wikidata_id=f"Q{uuid.uuid4().hex[:10]}")
    if local_paris:  # only if genre routed it local
        _insert_edge(
            conn,
            from_c=local_paris["canonical_id"],
            from_l=fiction,
            to_c=real,
            to_l=_global_lens(conn),
            evidence=doc,
        )
    after = snap()
    assert before[0] == after[0], "entity_projection must be unchanged by an edge"
    # canonical: pre-existing rows unchanged (a new real-Paris row may be appended).
    assert all(row in after[1] for row in before[1]), "no canonical row rewritten/deleted"


# --- RLS asymmetry: seeing one lens is not seeing the edge ------------------


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
        "SELECT %s, id FROM roles WHERE name='member' ON CONFLICT DO NOTHING",
        (pid,),
    )
    return pid


def _owned_doc_in_lens(conn: Any, text: str, owner: str, lens: str) -> str:
    doc = _itdb.make_doc(conn, text)
    conn.execute("UPDATE documents SET owner_principal_id=%s WHERE id=%s", (owner, doc))
    conn.execute(
        "INSERT INTO document_collection_members (collection_id, document_id) VALUES (%s,%s)",
        (lens, doc),
    )
    return doc


async def _list_as(principal_id: str, canonical_id: int, lens_id: str) -> int:
    aconn = await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )
    try:
        await aconn.execute("SET ROLE docray_api")
        await aconn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
        rows = await allusion_read.list_allusions(
            aconn, canonical_id=canonical_id, lens_id=lens_id, direction=Direction.BOTH
        )
        return len(rows)
    finally:
        await aconn.execute("SELECT set_config('app.principal_id', '', false)")
        await aconn.execute("RESET ROLE")
        await aconn.close()


def test_rls_caller_seeing_only_one_lens_sees_no_edge(conn):
    conn.execute(_ORG_CORPUS_MIGRATION.read_text(encoding="utf-8"))
    alice = _make_member(conn, f"alice-{uuid.uuid4()}")
    bob = _make_member(conn, f"bob-{uuid.uuid4()}")
    lens_a, lens_b = _lens(conn, "alice-work"), _lens(conn, "bob-work")
    doc_a = _owned_doc_in_lens(conn, "Alice's novel.", alice, lens_a)
    _owned_doc_in_lens(conn, "Bob's novel.", bob, lens_b)
    cid_a = _canonical(conn, "Duncan", "PERSON")
    cid_b = _canonical(conn, "Macbeth", "PERSON")
    real = _canonical(conn, "Scotland", "GPE", wikidata_id=f"Q{uuid.uuid4().hex[:10]}")

    # edge1: alice-work -> bob-work (cross-fiction). alice sees only lens_a.
    _insert_edge(conn, from_c=cid_a, from_l=lens_a, to_c=cid_b, to_l=lens_b, evidence=doc_a)
    # edge2: alice-work -> global (the real Scotland). Both sides visible to alice.
    _insert_edge(
        conn, from_c=cid_a, from_l=lens_a, to_c=real, to_l=_global_lens(conn), evidence=doc_a
    )

    try:
        # Alice: sees edge2 (lens_a + global, both visible) but NOT edge1 (lens_b hidden).
        assert asyncio.run(_list_as(alice, cid_a, lens_a)) == 1, "alice sees only the global edge"
        # Bob: cannot read doc_a (alice's) so cannot see lens_a; both edges anchor on lens_a → 0.
        assert asyncio.run(_list_as(bob, cid_a, lens_a)) == 0, "bob sees neither edge"
    finally:
        conn.execute("DELETE FROM entity_allusion WHERE from_lens_id=%s", (lens_a,))
        conn.execute(
            "UPDATE documents SET owner_principal_id=NULL WHERE owner_principal_id IN (%s,%s)",
            (alice, bob),
        )
        conn.execute("DELETE FROM principals WHERE id IN (%s,%s)", (alice, bob))


# --- heuristic proposer (FR-5) ---------------------------------------------


def test_heuristic_proposer_emits_proposed_alludes_to_edges(conn):
    # Unique surface so exactly one KB-linked canonical matches (the shared test DB
    # accumulates rows across runs; a fixed surface would match a prior run's entity).
    surface = f"Caladan{uuid.uuid4().hex[:8]}"
    fiction = _lens(conn, "dune")
    doc = _itdb.make_doc(conn, f"{surface} is a place.")
    conn.execute(
        "INSERT INTO document_collection_members (collection_id, document_id) VALUES (%s,%s)",
        (fiction, doc),
    )
    conn.execute(
        "INSERT INTO document_classification (document_id, genre, method, status) "
        "VALUES (%s,'fiction','heuristic','pending')",
        (doc,),
    )
    ann = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s,%s,'spacy','test')",
        (ann, doc),
    )
    span = conn.execute(
        "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,0,7) RETURNING id", (doc,)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
        "VALUES (%s,%s,%s,'GPE',%s)",
        (new_ulid_str(), ann, span, surface),
    )
    # A real-world entity sharing the surface (KB-linked) — the allusion target.
    real = _canonical(conn, surface, "GPE", wikidata_id=f"Q{uuid.uuid4().hex[:10]}")
    ap.refresh_projections_for_document(conn, doc)  # mints the local Caladan

    result = allusion_store.propose_allusions_for_document(conn, doc)
    assert result["proposed"] >= 1
    row = conn.execute(
        "SELECT to_canonical_id, edge_type, status, method FROM entity_allusion "
        "WHERE evidence_document_id=%s",
        (doc,),
    ).fetchone()
    assert row["to_canonical_id"] == real
    assert row["edge_type"] == "alludes_to" and row["status"] == "proposed"
    assert row["method"].startswith("heuristic:")
