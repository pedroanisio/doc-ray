"""Per-owner corpus isolation (migration 102).

doc-ray is a SaaS with open registration: a newly registered user must land on
a **clean, private corpus**. Migration 076 broke that invariant — its role-read
branch let any principal holding the ``read`` permission (i.e. every registered
reader) see every non-sensitive document in the tenant. With one tenant, zero
ACL rows and zero sensitivity tags, that resolved to "everyone sees everything".

Migration 102 replaces the blanket branch with ownership: a document is
readable by its owner, by an explicit ACL grantee, or by a tenant admin.
``owner_principal_id IS NULL`` means *unowned* — reserved for the future org
corpus, and today visible to admins only.

The RLS tests run against the live schema (skipped without the isolated test
DB, like every other DB-backed suite).
"""

from __future__ import annotations

import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION = _ROOT / "db" / "migrations" / "102_document_ownership.sql"


def test_migration_102_replaces_the_blanket_read_branch_with_ownership() -> None:
    migration = _MIGRATION.read_text(encoding="utf-8")
    assert "owner_principal_id" in migration
    assert "docray_readable_documents" in migration
    assert "docray_writable_documents" in migration, "owners must be able to write their own docs"
    # The 076 role-permission branch is exactly what leaked the corpus: any
    # principal with the 'read' action saw every document. It must be gone.
    assert "p.action = 'read'" not in migration, "the blanket role-read branch must not survive"


def _apply_migration_102(conn) -> None:
    """Re-apply 102 so each test is self-contained regardless of run order.

    Sibling RLS suites re-run migrations 061/064/076 inline, which re-create
    ``docray_readable_documents()`` from their older definitions and drop the
    ownership branch in the shared test DB (the pattern test_rbac/test_abac
    already use for their own migrations).
    """
    conn.execute(_MIGRATION.read_text(encoding="utf-8"))


def _reader(conn, label: str) -> str:
    """Create a registered-user principal: reader role, zero ACL rows."""
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


def _admin(conn, label: str) -> str:
    row = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) "
        "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
        (f"{label}-{uuid.uuid4()}",),
    ).fetchone()
    principal_id = str(row["id"])
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) "
        "SELECT %s, id FROM roles WHERE name = 'admin' ON CONFLICT DO NOTHING",
        (principal_id,),
    )
    return principal_id


def _own(conn, doc_id: str, principal_id: str | None) -> None:
    conn.execute(
        "UPDATE documents SET owner_principal_id = %s WHERE id = %s", (principal_id, doc_id)
    )


def _visible(conn, principal_id: str, doc_id: str) -> bool:
    """Read *doc_id* as *principal_id* through the API's exact RLS binding.

    ``docray_current_principal()`` only honors ``app.principal_id`` under the
    trusted ``docray_api`` role (migration 030); ``docray_client`` ignores it
    (anti-impersonation), so this is how a logged-in read binds identity.
    """
    conn.execute("SET ROLE docray_api")
    try:
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
        row = conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone()
        return row is not None
    finally:
        conn.execute("SELECT set_config('app.principal_id', '', false)")
        conn.execute("RESET ROLE")


def test_a_reader_cannot_see_another_readers_document(pg) -> None:
    """The incident: every registered reader could read the whole corpus."""
    import _itdb

    conn, created = pg
    _apply_migration_102(conn)
    doc_id = _itdb.make_doc(conn, "Alice's private upload.")
    created.append(doc_id)
    alice = _reader(conn, "owner-alice")
    bob = _reader(conn, "owner-bob")
    _own(conn, doc_id, alice)
    try:
        assert _visible(conn, alice, doc_id), "the owner must see their own document"
        assert not _visible(conn, bob, doc_id), (
            "a second registered reader must land on a clean corpus"
        )
    finally:
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (alice, bob))


def test_unowned_documents_are_admin_only(pg) -> None:
    """``owner_principal_id IS NULL`` is reserved for the future org corpus."""
    import _itdb

    conn, created = pg
    _apply_migration_102(conn)
    doc_id = _itdb.make_doc(conn, "Unowned legacy document.")
    created.append(doc_id)
    _own(conn, doc_id, None)
    reader = _reader(conn, "unowned-reader")
    admin = _admin(conn, "unowned-admin")
    try:
        assert not _visible(conn, reader, doc_id), "an unowned document is not a public document"
        assert _visible(conn, admin, doc_id), "a tenant admin still sees unowned documents"
    finally:
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (reader, admin))


def test_explicit_acl_grant_still_shares_a_document(pg) -> None:
    """Sharing survives: an ACL grant is how one user hands a doc to another."""
    import _itdb

    conn, created = pg
    _apply_migration_102(conn)
    doc_id = _itdb.make_doc(conn, "Shared upload.")
    created.append(doc_id)
    alice = _reader(conn, "acl-alice")
    bob = _reader(conn, "acl-bob")
    _own(conn, doc_id, alice)
    try:
        assert not _visible(conn, bob, doc_id)
        conn.execute(
            "INSERT INTO document_acl (document_id, principal_id, permission) "
            "VALUES (%s, %s, 'read') ON CONFLICT DO NOTHING",
            (doc_id, bob),
        )
        assert _visible(conn, bob, doc_id), "an explicit ACL grant must still work"
    finally:
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (alice, bob))


def _writable(conn, principal_id: str, doc_id: str) -> bool:
    """Whether the write policies would admit *principal_id* to *doc_id*.

    Asserted against ``docray_writable_documents()`` rather than by issuing an
    UPDATE: the client role holds no table-level UPDATE on ``documents`` today
    (every write still runs as the table owner), so an UPDATE here would fail
    on a GRANT and tell us nothing about the policy.
    """
    conn.execute("SET ROLE docray_api")
    try:
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
        row = conn.execute(
            "SELECT 1 FROM docray_writable_documents() d(id) WHERE d.id = %s", (doc_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.execute("SELECT set_config('app.principal_id', '', false)")
        conn.execute("RESET ROLE")


def test_owner_may_write_their_own_document(pg) -> None:
    """Owning a document implies curating it — but only the owner's own."""
    import _itdb

    conn, created = pg
    _apply_migration_102(conn)
    doc_id = _itdb.make_doc(conn, "Owner write probe.")
    created.append(doc_id)
    alice = _reader(conn, "write-alice")
    bob = _reader(conn, "write-bob")
    _own(conn, doc_id, alice)
    try:
        assert _writable(conn, alice, doc_id), "the owner must be able to curate their own document"
        assert not _writable(conn, bob, doc_id), "a non-owner must not be able to write it"
    finally:
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (alice, bob))


# ---------------------------------------------------------------------------
# Phase 1 — the `member` role (migration 104). Ownership is only useful if a
# registered user can put something into their own corpus.
# ---------------------------------------------------------------------------

_MIGRATION_104 = _ROOT / "db" / "migrations" / "104_member_role.sql"


def test_migration_104_grants_member_write_actions_but_not_admin() -> None:
    migration = _MIGRATION_104.read_text(encoding="utf-8")
    assert "'member'" in migration
    assert "'read', 'annotate', 'delete'" in migration
    assert "'redact'" not in migration, "redact stays admin-only"
    assert "p.action IN ('read', 'annotate', 'delete', 'redact', 'admin')" not in migration


def _apply_migration_104(conn) -> None:
    conn.execute(_MIGRATION_104.read_text(encoding="utf-8"))


def _member(conn, label: str) -> str:
    row = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) "
        "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
        (f"{label}-{uuid.uuid4()}",),
    ).fetchone()
    principal_id = str(row["id"])
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) "
        "SELECT %s, id FROM roles WHERE name = 'member' ON CONFLICT DO NOTHING",
        (principal_id,),
    )
    return principal_id


def _actions(conn, principal_id: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT p.action FROM principal_roles pr
          JOIN role_permissions rp ON rp.role_id = pr.role_id
          JOIN permissions p       ON p.id = rp.permission_id
         WHERE pr.principal_id = %s
        """,
        (principal_id,),
    ).fetchall()
    return {str(row["action"]) for row in rows}


def test_member_carries_write_actions_and_reader_does_not(pg) -> None:
    conn, _created = pg
    _apply_migration_102(conn)
    _apply_migration_104(conn)
    member = _member(conn, "role-member")
    reader = _reader(conn, "role-reader")
    try:
        assert "annotate" in _actions(conn, member)
        assert "delete" in _actions(conn, member)
        assert "admin" not in _actions(conn, member), "a member is not an admin"
        assert _actions(conn, reader) == {"read"}, "reader must stay read-only"
    finally:
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (member, reader))


def test_member_write_power_is_still_bounded_by_ownership(pg) -> None:
    """The role grants the *capability*; RLS still decides which rows.

    This is the guarantee that makes promoting every registered user to member
    safe: 'annotate'/'delete' are not consulted by any read or write policy —
    only docray_is_admin() reads the permission tables — so a member's reach is
    exactly the documents they own or were granted.
    """
    import _itdb

    conn, created = pg
    _apply_migration_102(conn)
    _apply_migration_104(conn)
    doc_id = _itdb.make_doc(conn, "Member boundary probe.")
    created.append(doc_id)
    owner = _member(conn, "bound-owner")
    stranger = _member(conn, "bound-stranger")
    _own(conn, doc_id, owner)
    try:
        assert _visible(conn, owner, doc_id)
        assert _writable(conn, owner, doc_id)
        assert not _visible(conn, stranger, doc_id), "member role must not confer corpus-wide read"
        assert not _writable(conn, stranger, doc_id)
    finally:
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, stranger))
