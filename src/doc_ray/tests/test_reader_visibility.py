"""Reader-role corpus visibility (auth-audit MAJOR).

Before migration 076, ``docray_readable_documents()`` was admin ∪ per-document
ACL only — a freshly registered reader (principal + reader role, zero ACL
rows) saw an EMPTY corpus, and no granting flow existed. 076 adds a role-based
branch: any principal whose role carries the ``read`` permission sees the
tenant's corpus, EXCEPT documents marked ``sensitivity=sensitive`` (those stay
ACL-gated per migration 064's ABAC policy).

The RLS tests run against the live schema (skip locally without the isolated
test DB, like every other DB-backed suite).
"""

from __future__ import annotations

import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def test_migration_076_adds_role_read_branch_without_leaking_sensitive() -> None:
    migration = (_ROOT / "db" / "migrations" / "076_reader_corpus_read.sql").read_text(
        encoding="utf-8"
    )
    assert "docray_readable_documents" in migration
    assert "role_permissions" in migration
    assert "p.action = 'read'" in migration
    assert "sensitivity" in migration
    assert "lower(dm.value) = 'sensitive'" in migration
    # Writable documents stay ACL/admin-gated — 076 must not touch them.
    assert "docray_writable_documents" not in migration


def test_migration_077_bumps_schema_version() -> None:
    migration = (_ROOT / "db" / "migrations" / "077_schema_version_1_24_0.sql").read_text(
        encoding="utf-8"
    )
    assert "1.24.0" in migration
    decisions = (_ROOT / "engineering_decisions.yml").read_text(encoding="utf-8")
    assert 'source_schema_version: "1.24.0"' in decisions


def _ensure_migration_076(conn) -> None:
    """Re-apply 076 before each RLS test.

    Sibling RLS tests re-run migrations 061/064 inline, which re-create
    docray_readable_documents() from their older definitions and drop this
    migration's role-read branch in the shared test DB. Re-applying 076 here
    makes each test self-contained regardless of run order (the same pattern
    test_rbac/test_abac already use for their own migrations).
    """
    conn.execute((_ROOT / "db" / "migrations" / "076_reader_corpus_read.sql").read_text())


def _reader_principal(conn, display_name: str) -> str:
    row = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) "
        "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
        (display_name,),
    ).fetchone()
    principal_id = str(row["id"])
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) "
        "SELECT %s, id FROM roles WHERE name = 'reader' ON CONFLICT DO NOTHING",
        (principal_id,),
    )
    return principal_id


def _visible(conn, principal_id: str, doc_id: str) -> bool:
    # Mirror the API's apply_principal path exactly: docray_current_principal()
    # only honors app.principal_id under the trusted docray_api role (migration
    # 030); docray_client deliberately ignores it (anti-impersonation), so a
    # logged-in browser/API read binds identity this way.
    conn.execute("SET ROLE docray_api")
    try:
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
        row = conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone()
        return row is not None
    finally:
        conn.execute("SELECT set_config('app.principal_id', '', false)")
        conn.execute("RESET ROLE")


def test_reader_role_sees_the_tenant_corpus_without_acl_rows(pg) -> None:
    import _itdb

    conn, created = pg
    _ensure_migration_076(conn)
    doc_id = _itdb.make_doc(conn, "Reader visibility probe.")
    created.append(doc_id)
    reader = _reader_principal(conn, f"reader-vis-{uuid.uuid4()}")
    nobody = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) "
        "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
        (f"roleless-vis-{uuid.uuid4()}",),
    ).fetchone()
    roleless = str(nobody["id"])
    try:
        assert _visible(conn, reader, doc_id), "reader role must see the tenant corpus"
        assert not _visible(conn, roleless, doc_id), "a principal without roles sees nothing"
    finally:
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (reader, roleless))


def test_reader_role_does_not_see_sensitive_documents(pg) -> None:
    import _itdb

    conn, created = pg
    _ensure_migration_076(conn)
    doc_id = _itdb.make_doc(conn, "Sensitive reader probe.")
    created.append(doc_id)
    conn.execute(
        "INSERT INTO document_metadata (document_id, key, value) "
        "VALUES (%s, 'sensitivity', 'sensitive') "
        "ON CONFLICT (document_id, key) DO UPDATE SET value = EXCLUDED.value",
        (doc_id,),
    )
    reader = _reader_principal(conn, f"reader-sens-{uuid.uuid4()}")
    try:
        assert not _visible(conn, reader, doc_id), (
            "sensitivity=sensitive documents stay ACL-gated (migration 064)"
        )
        conn.execute(
            "INSERT INTO document_acl (document_id, principal_id, permission) "
            "VALUES (%s, %s, 'read') ON CONFLICT DO NOTHING",
            (doc_id, reader),
        )
        assert _visible(conn, reader, doc_id), "an explicit ACL grant still works"
    finally:
        conn.execute("DELETE FROM principals WHERE id = %s", (reader,))
