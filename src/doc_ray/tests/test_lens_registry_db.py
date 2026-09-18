"""DB-backed integration tests for the Lens Registry (issue #5).

Exercises migration 121 against the real schema on the isolated test Postgres
(via the ``pg`` fixture / ``_itdb``): every FR-2 integrity rule must be enforced
by the database, not merely by the app, so each negative case asserts that
*Postgres* raises. Also covers migration idempotence, lens overlap, the reserved
``global`` lens, the FK ``SET NULL`` cascades, and — the required RLS/tenancy
case — that a document's ``default_lens_id`` never leaks across principals while
the lens *name* is deliberately shared corpus metadata (decision D-3, recorded).

Skips cleanly when no safe test database is reachable, like every DB-backed suite.
"""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_121 = _ROOT / "db" / "migrations" / "121_lens_registry.sql"
_MIGRATION_102 = _ROOT / "db" / "migrations" / "102_document_ownership.sql"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _mk_collection(
    conn, *, is_scope: bool = False, kind: str = "manual", parent: str | None = None
) -> str:
    """Create a uniquely-named collection and return its id."""
    name = f"bl-b-{uuid.uuid4()}"
    row = conn.execute(
        "INSERT INTO document_collections (name, kind, is_scope, parent_lens_id) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (name, kind, is_scope, parent),
    ).fetchone()
    return str(row["id"])


def _add_member(conn, collection_id: str, document_id: str) -> None:
    conn.execute(
        "INSERT INTO document_collection_members (collection_id, document_id) "
        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (collection_id, document_id),
    )


def _cleanup(conn, collection_ids: list[str]) -> None:
    """Delete test collections (never the reserved global lens)."""
    for cid in collection_ids:
        with contextlib.suppress(psycopg.Error):
            conn.execute(
                "DELETE FROM document_collections WHERE id = %s AND name <> 'global'", (cid,)
            )


# --------------------------------------------------------------------------
# Migration shape, idempotence, and the reserved global lens
# --------------------------------------------------------------------------


def test_migration_is_idempotent(pg) -> None:
    """Re-running the migration is a no-op: no error, and global stays a single row."""
    conn, _ = pg
    conn.execute(_MIGRATION_121.read_text(encoding="utf-8"))
    conn.execute(_MIGRATION_121.read_text(encoding="utf-8"))  # twice
    n = conn.execute(
        "SELECT count(*) AS n FROM document_collections WHERE name = 'global'"
    ).fetchone()["n"]
    assert n == 1


def test_columns_and_reserved_global_lens_exist(pg) -> None:
    conn, _ = pg
    cols = {
        r["column_name"]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name IN ('document_collections','documents') "
            "AND column_name IN ('is_scope','parent_lens_id','default_lens_id')"
        ).fetchall()
    }
    assert cols == {"is_scope", "parent_lens_id", "default_lens_id"}
    g = conn.execute(
        "SELECT is_scope, kind FROM document_collections WHERE name = 'global'"
    ).fetchone()
    assert g["is_scope"] is True and g["kind"] == "manual"


def test_global_lens_cannot_be_deleted(pg) -> None:
    conn, _ = pg
    with pytest.raises(psycopg.Error, match="global lens cannot be deleted"):
        conn.execute("DELETE FROM document_collections WHERE name = 'global'")


def test_global_lens_cannot_be_renamed(pg) -> None:
    conn, _ = pg
    with pytest.raises(psycopg.Error, match="global lens cannot be renamed"):
        conn.execute("UPDATE document_collections SET name = 'earth' WHERE name = 'global'")


def test_global_lens_cannot_be_demoted(pg) -> None:
    conn, _ = pg
    with pytest.raises(psycopg.Error, match="global lens must remain a scope"):
        conn.execute("UPDATE document_collections SET is_scope = false WHERE name = 'global'")


# --------------------------------------------------------------------------
# parent_lens_id integrity (FR-2)
# --------------------------------------------------------------------------


def test_parent_lens_must_reference_a_scope(pg) -> None:
    conn, _ = pg
    created: list[str] = []
    try:
        plain = _mk_collection(conn, is_scope=False)
        child = _mk_collection(conn, is_scope=True)
        created += [plain, child]
        with pytest.raises(psycopg.Error, match="must reference a scope collection"):
            conn.execute(
                "UPDATE document_collections SET parent_lens_id = %s WHERE id = %s",
                (plain, child),
            )
    finally:
        _cleanup(conn, created)


def test_only_a_scope_may_have_a_parent(pg) -> None:
    conn, _ = pg
    created: list[str] = []
    try:
        parent = _mk_collection(conn, is_scope=True)
        plain_child = _mk_collection(conn, is_scope=False)
        created += [parent, plain_child]
        with pytest.raises(psycopg.Error, match="only a scope collection"):
            conn.execute(
                "UPDATE document_collections SET parent_lens_id = %s WHERE id = %s",
                (parent, plain_child),
            )
    finally:
        _cleanup(conn, created)


def test_a_valid_parent_lens_is_accepted(pg) -> None:
    conn, _ = pg
    created: list[str] = []
    try:
        parent = _mk_collection(conn, is_scope=True)
        child = _mk_collection(conn, is_scope=True)
        created += [parent, child]
        conn.execute(
            "UPDATE document_collections SET parent_lens_id = %s WHERE id = %s", (parent, child)
        )
        got = conn.execute(
            "SELECT parent_lens_id FROM document_collections WHERE id = %s", (child,)
        ).fetchone()["parent_lens_id"]
        assert str(got) == parent
    finally:
        _cleanup(conn, created)


def test_self_parenting_is_rejected(pg) -> None:
    conn, _ = pg
    created: list[str] = []
    try:
        lens = _mk_collection(conn, is_scope=True)
        created.append(lens)
        with pytest.raises(psycopg.Error, match="lens cycle"):
            conn.execute(
                "UPDATE document_collections SET parent_lens_id = %s WHERE id = %s", (lens, lens)
            )
    finally:
        _cleanup(conn, created)


def test_three_node_parent_cycle_is_rejected(pg) -> None:
    conn, _ = pg
    created: list[str] = []
    try:
        a = _mk_collection(conn, is_scope=True)
        b = _mk_collection(conn, is_scope=True)
        c = _mk_collection(conn, is_scope=True)
        created += [a, b, c]
        # a -> b -> c, then closing c -> a must be rejected.
        conn.execute("UPDATE document_collections SET parent_lens_id = %s WHERE id = %s", (b, a))
        conn.execute("UPDATE document_collections SET parent_lens_id = %s WHERE id = %s", (c, b))
        with pytest.raises(psycopg.Error, match="lens cycle"):
            conn.execute(
                "UPDATE document_collections SET parent_lens_id = %s WHERE id = %s", (a, c)
            )
    finally:
        _cleanup(conn, created)


def test_demoting_a_parent_lens_is_rejected(pg) -> None:
    conn, _ = pg
    created: list[str] = []
    try:
        parent = _mk_collection(conn, is_scope=True)
        child = _mk_collection(conn, is_scope=True, parent=parent)
        created += [parent, child]
        with pytest.raises(psycopg.Error, match="still parents a lens"):
            conn.execute(
                "UPDATE document_collections SET is_scope = false WHERE id = %s", (parent,)
            )
    finally:
        _cleanup(conn, created)


def test_deleting_a_parent_lens_nulls_the_childs_parent(pg) -> None:
    """The plain FK's ON DELETE SET NULL keeps a child lens; it just detaches it."""
    conn, _ = pg
    created: list[str] = []
    try:
        parent = _mk_collection(conn, is_scope=True)
        child = _mk_collection(conn, is_scope=True, parent=parent)
        created.append(child)  # parent is deleted below
        conn.execute("DELETE FROM document_collections WHERE id = %s", (parent,))
        got = conn.execute(
            "SELECT parent_lens_id FROM document_collections WHERE id = %s", (child,)
        ).fetchone()
        assert got["parent_lens_id"] is None
    finally:
        _cleanup(conn, created)


# --------------------------------------------------------------------------
# default_lens_id integrity (FR-2) + overlap
# --------------------------------------------------------------------------


def test_default_lens_must_be_a_scope(pg) -> None:
    import _itdb

    conn, created_docs = pg
    created: list[str] = []
    try:
        doc = _itdb.make_doc(conn, "A default-lens integrity doc.")
        created_docs.append(doc)
        plain = _mk_collection(conn, is_scope=False)
        created.append(plain)
        _add_member(conn, plain, doc)  # member, but not a scope
        with pytest.raises(psycopg.Error, match="must reference a scope collection"):
            conn.execute("UPDATE documents SET default_lens_id = %s WHERE id = %s", (plain, doc))
    finally:
        _cleanup(conn, created)


def test_default_lens_must_be_a_member(pg) -> None:
    import _itdb

    conn, created_docs = pg
    created: list[str] = []
    try:
        doc = _itdb.make_doc(conn, "A non-member default-lens doc.")
        created_docs.append(doc)
        lens = _mk_collection(conn, is_scope=True)
        created.append(lens)
        # not a member yet -> rejected
        with pytest.raises(psycopg.Error, match="is not a member of lens"):
            conn.execute("UPDATE documents SET default_lens_id = %s WHERE id = %s", (lens, doc))
        # add membership -> now accepted
        _add_member(conn, lens, doc)
        conn.execute("UPDATE documents SET default_lens_id = %s WHERE id = %s", (lens, doc))
        got = conn.execute(
            "SELECT default_lens_id FROM documents WHERE id = %s", (doc,)
        ).fetchone()["default_lens_id"]
        assert str(got) == lens
    finally:
        _cleanup(conn, created)


def test_a_document_may_belong_to_several_lenses(pg) -> None:
    """Lenses overlap: a document sits in more than one, both legal and durable."""
    import _itdb

    conn, created_docs = pg
    created: list[str] = []
    try:
        doc = _itdb.make_doc(conn, "A document seen through two lenses.")
        created_docs.append(doc)
        lens_a = _mk_collection(conn, is_scope=True)
        lens_b = _mk_collection(conn, is_scope=True)
        created += [lens_a, lens_b]
        _add_member(conn, lens_a, doc)
        _add_member(conn, lens_b, doc)
        rows = conn.execute(
            "SELECT collection_id FROM document_collection_members WHERE document_id = %s", (doc,)
        ).fetchall()
        member_of = {str(r["collection_id"]) for r in rows}
        assert {lens_a, lens_b} <= member_of
        # either overlapping lens is a legal default.
        conn.execute("UPDATE documents SET default_lens_id = %s WHERE id = %s", (lens_a, doc))
        conn.execute("UPDATE documents SET default_lens_id = %s WHERE id = %s", (lens_b, doc))
    finally:
        _cleanup(conn, created)


def test_removing_membership_clears_the_default_lens(pg) -> None:
    """Unmembering a document from its default lens must not strand the pointer."""
    import _itdb

    conn, created_docs = pg
    created: list[str] = []
    try:
        doc = _itdb.make_doc(conn, "A doc whose default lens loses membership.")
        created_docs.append(doc)
        lens = _mk_collection(conn, is_scope=True)
        created.append(lens)
        _add_member(conn, lens, doc)
        conn.execute("UPDATE documents SET default_lens_id = %s WHERE id = %s", (lens, doc))
        conn.execute(
            "DELETE FROM document_collection_members WHERE collection_id = %s AND document_id = %s",
            (lens, doc),
        )
        got = conn.execute(
            "SELECT default_lens_id FROM documents WHERE id = %s", (doc,)
        ).fetchone()["default_lens_id"]
        assert got is None
    finally:
        _cleanup(conn, created)


def test_deleting_a_lens_nulls_the_default_lens(pg) -> None:
    import _itdb

    conn, created_docs = pg
    created: list[str] = []
    try:
        doc = _itdb.make_doc(conn, "A doc whose default lens is deleted.")
        created_docs.append(doc)
        lens = _mk_collection(conn, is_scope=True)
        _add_member(conn, lens, doc)
        conn.execute("UPDATE documents SET default_lens_id = %s WHERE id = %s", (lens, doc))
        conn.execute("DELETE FROM document_collections WHERE id = %s", (lens,))
        got = conn.execute(
            "SELECT default_lens_id FROM documents WHERE id = %s", (doc,)
        ).fetchone()["default_lens_id"]
        assert got is None
    finally:
        _cleanup(conn, created)


# --------------------------------------------------------------------------
# RLS / tenancy (D-3): the lens name is shared metadata; a document's
# default_lens_id is not. A second principal never sees the first's doc.
# --------------------------------------------------------------------------


def _reader(conn, label: str) -> str:
    row = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) "
        "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
        (f"{label}-{uuid.uuid4()}",),
    ).fetchone()
    principal_id = str(row["id"])
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) "
        "SELECT %s, id FROM roles WHERE name = 'reader' ON CONFLICT DO NOTHING",
        (principal_id,),
    )
    return principal_id


def _default_lens_visible_as(conn, principal_id: str, doc_id: str) -> tuple[bool, object]:
    """Return (row_visible, default_lens_id) reading as *principal_id* under docray_api."""
    conn.execute("SET ROLE docray_api")
    try:
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
        row = conn.execute(
            "SELECT default_lens_id FROM documents WHERE id = %s", (doc_id,)
        ).fetchone()
        return (row is not None, row["default_lens_id"] if row else None)
    finally:
        conn.execute("SELECT set_config('app.principal_id', '', false)")
        conn.execute("RESET ROLE")


def _lens_visible_as(conn, principal_id: str, collection_id: str) -> bool:
    conn.execute("SET ROLE docray_api")
    try:
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
        row = conn.execute(
            "SELECT id FROM document_collections WHERE id = %s", (collection_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.execute("SELECT set_config('app.principal_id', '', false)")
        conn.execute("RESET ROLE")


def test_default_lens_id_does_not_leak_across_principals(pg) -> None:
    import _itdb

    conn, created_docs = pg
    conn.execute(_MIGRATION_102.read_text(encoding="utf-8"))  # ensure ownership RLS is current
    created: list[str] = []
    alice = bob = None
    try:
        doc = _itdb.make_doc(conn, "Alice's document with a default lens.")
        created_docs.append(doc)
        alice = _reader(conn, "lens-alice")
        bob = _reader(conn, "lens-bob")
        conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (alice, doc))
        lens = _mk_collection(conn, is_scope=True)
        created.append(lens)
        _add_member(conn, lens, doc)
        conn.execute("UPDATE documents SET default_lens_id = %s WHERE id = %s", (lens, doc))

        alice_sees, alice_lens = _default_lens_visible_as(conn, alice, doc)
        bob_sees, bob_lens = _default_lens_visible_as(conn, bob, doc)
        assert alice_sees and str(alice_lens) == lens, "the owner reads their own default lens"
        assert not bob_sees and bob_lens is None, (
            "a second principal must not see the document or its default_lens_id"
        )
        # D-3 finding, asserted rather than patched: the lens *name* is shared
        # corpus navigation metadata (document_collections has no tenant/RLS),
        # so both principals can see the lens row itself.
        assert _lens_visible_as(conn, bob, lens), (
            "lenses are shared navigation metadata by the migration-047 design (D-3)"
        )
    finally:
        _cleanup(conn, created)
        if alice and bob:
            conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (alice, bob))
