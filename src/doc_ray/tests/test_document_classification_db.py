"""DB-backed tests for document_classification (SLP Block C, #6).

Exercises the real table (migration 127) against a throwaway Postgres: the
machine upsert + read, the database CHECK constraints, the load-bearing
verification invariant (a machine re-run never clobbers a human-confirmed row),
and RLS (no cross-principal read/write). Skips when no safe test DB is reachable.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

_itdb = pytest.importorskip("_itdb")

from doc_ray.adapters import document_classification_store as store  # noqa: E402
from doc_ray.application.genre_classification import GenreClassification  # noqa: E402
from doc_ray.domain.genre_policy import Genre  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]
_ORG_CORPUS_MIGRATION = _ROOT / "db" / "migrations" / "110_org_corpus.sql"


def _restore_readable_documents(conn: Any) -> None:
    """Re-apply the org-corpus migration so this test is self-contained.

    Sibling RLS suites (test_rbac, test_abac) re-run migrations 061/064/076 inline
    against the shared test DB, reverting ``docray_readable_documents()`` to its
    pre-ownership definition and dropping the owner branch — so a member could not
    see their own document. Restore the current definition first (the same guard
    test_document_ownership uses for migration 102).
    """
    conn.execute(_ORG_CORPUS_MIGRATION.read_text(encoding="utf-8"))


def _machine(
    genre: Genre, *, subtype: str | None = None, confidence: float = 0.9
) -> GenreClassification:
    return GenreClassification(
        genre=genre,
        subtype=subtype,
        confidence=confidence,
        method="heuristic",
        rationale="test",
        status="pending",
    )


@pytest.fixture
def conn():
    c = _itdb.connect()
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def test_machine_upsert_and_read_roundtrip(conn):
    doc = _itdb.make_doc(conn, "A novel with chapters.")
    assert store.get_classification(conn, doc) is None

    wrote = store.upsert_machine_classification(conn, doc, _machine(Genre.FICTION, subtype="novel"))
    assert wrote is True

    row = store.get_classification(conn, doc)
    assert row is not None
    assert row["genre"] == "fiction"
    assert row["subtype"] == "novel"
    assert row["method"] == "heuristic"
    assert row["status"] == "pending"
    assert row["reviewer"] is None


def test_machine_rerun_updates_a_pending_row(conn):
    doc = _itdb.make_doc(conn, "text")
    store.upsert_machine_classification(conn, doc, _machine(Genre.UNKNOWN, confidence=0.1))
    store.upsert_machine_classification(conn, doc, _machine(Genre.FICTION, confidence=0.95))
    row = store.get_classification(conn, doc)
    assert row["genre"] == "fiction"  # a pending guess is refreshable


@pytest.mark.parametrize(
    "genre,confidence,status",
    [
        ("sci_fi", 0.5, "pending"),  # genre not in the CHECK set
        ("fiction", 1.5, "pending"),  # confidence out of [0,1]
        ("fiction", 0.5, "maybe"),  # status not in the CHECK set
    ],
)
def test_check_constraints_reject_bad_values(conn, genre, confidence, status):
    doc = _itdb.make_doc(conn, "text")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO document_classification (document_id, genre, confidence, method, status) "
            "VALUES (%s, %s, %s, 'heuristic', %s)",
            (doc, genre, confidence, status),
        )


def test_machine_rerun_never_clobbers_a_confirmed_row(conn):
    """The load-bearing verification invariant (#6): re-classify must not overwrite
    a human decision."""
    doc = _itdb.make_doc(conn, "text")
    # A human confirms it non_fiction (simulated directly on the superuser conn).
    conn.execute(
        "INSERT INTO document_classification "
        "(document_id, genre, method, status, reviewer, rationale) "
        "VALUES (%s, 'non_fiction', 'human', 'confirmed', NULL, 'human call')",
        (doc,),
    )
    # A later classifier run says fiction, high confidence.
    wrote = store.upsert_machine_classification(conn, doc, _machine(Genre.FICTION, confidence=0.99))
    assert wrote is False  # the confirmed row was protected

    row = store.get_classification(conn, doc)
    assert row["genre"] == "non_fiction"  # human decision intact
    assert row["status"] == "confirmed"
    assert row["method"] == "human"


# --- RLS: a classification is readable/writable exactly when its document is ---


def _make_member(conn: Any, name: str) -> str:
    row = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) "
        "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
        (name,),
    ).fetchone()
    pid = str(row["id"])
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) "
        "SELECT %s, id FROM roles WHERE name = 'member' ON CONFLICT DO NOTHING",
        (pid,),
    )
    return pid


def _make_owned_doc(conn: Any, text: str, owner_id: str) -> str:
    doc_id = _itdb.make_doc(conn, text)
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner_id, doc_id))
    return doc_id


async def _as_principal(principal_id: str, fn):
    aconn = await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )
    try:
        await aconn.execute("SET ROLE docray_api")
        await aconn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
        return await fn(aconn)
    finally:
        await aconn.execute("SELECT set_config('app.principal_id', '', false)")
        await aconn.execute("RESET ROLE")
        await aconn.close()


def test_rls_no_cross_principal_read_or_write(conn):
    import asyncio

    _restore_readable_documents(conn)
    alice = _make_member(conn, f"alice-{uuid.uuid4()}")
    bob = _make_member(conn, f"bob-{uuid.uuid4()}")
    doc_a = _make_owned_doc(conn, "Alice's book.", alice)
    try:
        # Alice confirms her own document's genre — permitted.
        asyncio.run(
            _as_principal(
                alice,
                lambda c: store.confirm_classification_async(
                    c, doc_a, genre=Genre.FICTION, subtype="novel", reviewer=alice, rationale="mine"
                ),
            )
        )
        # Alice reads it back; Bob cannot see it at all (RLS SELECT).
        alice_row = asyncio.run(
            _as_principal(alice, lambda c: store.get_classification_async(c, doc_a))
        )
        bob_row = asyncio.run(
            _as_principal(bob, lambda c: store.get_classification_async(c, doc_a))
        )
        assert alice_row is not None and alice_row["genre"] == "fiction"
        assert bob_row is None, "Bob must not read Alice's classification"

        # Bob cannot write Alice's document's classification (RLS WITH CHECK).
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            asyncio.run(
                _as_principal(
                    bob,
                    lambda c: store.confirm_classification_async(
                        c,
                        doc_a,
                        genre=Genre.NON_FICTION,
                        subtype=None,
                        reviewer=bob,
                        rationale="nope",
                    ),
                )
            )
    finally:
        conn.execute("UPDATE documents SET owner_principal_id = NULL WHERE id = %s", (doc_a,))
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (alice, bob))
