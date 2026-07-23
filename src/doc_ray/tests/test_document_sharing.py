"""Owner-driven document sharing (migration 108, Phase 3).

Per-owner isolation (migration 102) gave each user a private corpus, but only the
admin CLI could write ``document_acl`` rows — so a user had no way to hand a
document to a colleague. This adds the missing grant/revoke path, enforced at the
database: a principal may share only documents they OWN (or, for an admin, any in
the tenant), and only the ``read``/``annotate`` permissions — never ``admin`` or
``redact``, which stay admin-CLI-only.

``document_acl`` is itself FORCE ROW LEVEL SECURITY (Phase 2's rule applied to the
new write path): granting ``docray_client`` INSERT without a policy would let any
principal forge a grant for someone else's document. The batch writer keeps its
BYPASSRLS exemption for the admin CLI.

The RLS tests run against the live schema (skipped without the isolated test DB,
like every other DB-backed suite).
"""

from __future__ import annotations

import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION = _ROOT / "db" / "migrations" / "108_document_sharing.sql"


def test_migration_108_grants_share_write_and_forces_acl_rls() -> None:
    migration = _MIGRATION.read_text(encoding="utf-8")
    assert "document_acl" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "docray_can_share_document" in migration
    assert "docray_principal_by_email" in migration
    # Sharing must not become a back door to admin/redact.
    assert "'read', 'annotate'" in migration


def _apply(conn) -> None:
    # 102 (ownership) and 106 (FORCE + batch role) are this migration's ground.
    for name in (
        "102_document_ownership.sql",
        "104_member_role.sql",
        "106_force_rls.sql",
        "108_document_sharing.sql",
    ):
        conn.execute("RESET ROLE")
        conn.execute((_ROOT / "db" / "migrations" / name).read_text(encoding="utf-8"))
    conn.execute("RESET ROLE")


def _member(conn, label: str, email: str | None = None) -> str:
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
    if email is not None:
        conn.execute(
            "INSERT INTO principal_credentials "
            "(principal_id, tenant_id, email_normalized, password_hash, password_algo, "
            " created_at, updated_at) "
            "SELECT %s, tenant_id, %s, '$argon2id$x', 'argon2id', now(), now() "
            "  FROM principals WHERE id = %s",
            (principal_id, email, principal_id),
        )
    return principal_id


def _as(conn, principal_id: str) -> None:
    conn.execute("SET ROLE docray_api")
    conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))


def _reset(conn) -> None:
    conn.execute("SELECT set_config('app.principal_id', '', false)")
    conn.execute("RESET ROLE")


def _visible(conn, principal_id: str, doc_id: str) -> bool:
    _as(conn, principal_id)
    try:
        return (
            conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone() is not None
        )
    finally:
        _reset(conn)


def test_owner_can_grant_read_and_the_grantee_gains_visibility(pg) -> None:
    import _itdb

    conn, created = pg
    _apply(conn)
    doc_id = _itdb.make_doc(conn, "Shareable document.")
    created.append(doc_id)
    owner = _member(conn, "share-owner")
    colleague = _member(conn, "share-colleague")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    try:
        assert not _visible(conn, colleague, doc_id), "no access before the grant"
        _as(conn, owner)
        conn.execute(
            "INSERT INTO document_acl (document_id, principal_id, permission) VALUES (%s, %s, 'read')",
            (doc_id, colleague),
        )
        _reset(conn)
        assert _visible(conn, colleague, doc_id), "the grant confers read"
    finally:
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, colleague))
        conn.execute("RESET ROLE")


def test_a_non_owner_cannot_grant_on_someone_elses_document(pg) -> None:
    import _itdb

    conn, created = pg
    _apply(conn)
    doc_id = _itdb.make_doc(conn, "Not yours to share.")
    created.append(doc_id)
    owner = _member(conn, "real-owner")
    attacker = _member(conn, "attacker")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    try:
        _as(conn, attacker)
        import psycopg

        raised = False
        try:
            conn.execute(
                "INSERT INTO document_acl (document_id, principal_id, permission) "
                "VALUES (%s, %s, 'read')",
                (doc_id, attacker),
            )
        except psycopg.errors.InsufficientPrivilege:
            raised = True
        _reset(conn)
        assert raised, "a non-owner must not be able to grant themselves access"
        assert not _visible(conn, attacker, doc_id)
    finally:
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, attacker))
        conn.execute("RESET ROLE")


def test_owner_cannot_grant_admin_or_redact_through_sharing(pg) -> None:
    """Sharing hands out read/annotate — never the escalating permissions."""
    import _itdb
    import psycopg

    conn, created = pg
    _apply(conn)
    doc_id = _itdb.make_doc(conn, "No privilege escalation via sharing.")
    created.append(doc_id)
    owner = _member(conn, "esc-owner")
    colleague = _member(conn, "esc-colleague")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    try:
        _as(conn, owner)
        for forbidden in ("admin", "redact"):
            raised = False
            try:
                conn.execute(
                    "INSERT INTO document_acl (document_id, principal_id, permission) "
                    "VALUES (%s, %s, %s)",
                    (doc_id, colleague, forbidden),
                )
            except psycopg.errors.InsufficientPrivilege:
                raised = True
            assert raised, f"an owner must not grant {forbidden!r} through sharing"
        _reset(conn)
    finally:
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, colleague))
        conn.execute("RESET ROLE")


def test_owner_can_revoke_a_share(pg) -> None:
    import _itdb

    conn, created = pg
    _apply(conn)
    doc_id = _itdb.make_doc(conn, "Revocable share.")
    created.append(doc_id)
    owner = _member(conn, "rev-owner")
    colleague = _member(conn, "rev-colleague")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    try:
        _as(conn, owner)
        conn.execute(
            "INSERT INTO document_acl (document_id, principal_id, permission) VALUES (%s, %s, 'read')",
            (doc_id, colleague),
        )
        _reset(conn)
        assert _visible(conn, colleague, doc_id)
        _as(conn, owner)
        conn.execute(
            "DELETE FROM document_acl WHERE document_id = %s AND principal_id = %s",
            (doc_id, colleague),
        )
        _reset(conn)
        assert not _visible(conn, colleague, doc_id), "revocation removes access"
    finally:
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, colleague))
        conn.execute("RESET ROLE")


def test_principal_by_email_resolves_within_the_tenant(pg) -> None:
    """The share-by-email lookup finds a tenant member and refuses unknowns."""
    conn, _created = pg
    _apply(conn)
    email = f"colleague-{uuid.uuid4().hex[:10]}@example.com"
    colleague = _member(conn, "email-colleague", email=email)
    caller = _member(conn, "email-caller")
    try:
        _as(conn, caller)
        found = conn.execute("SELECT docray_principal_by_email(%s) AS id", (email,)).fetchone()
        missing = conn.execute(
            "SELECT docray_principal_by_email(%s) AS id", ("nobody@example.com",)
        ).fetchone()
        _reset(conn)
        assert found["id"] is not None and str(found["id"]) == colleague
        assert missing["id"] is None
    finally:
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (colleague, caller))
        conn.execute("RESET ROLE")
