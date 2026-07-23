"""Organization corpus: opt-in tenant-wide visibility (migration 110, BL-139).

Per-owner isolation (102) plus sharing (108) covers private documents and
person-to-person grants. The organization corpus is the third scope: an owner
publishes a document to their whole tenant with ``visibility = 'org'``, and every
principal in that tenant can read it — nobody outside it.

This is the SAFE inverse of the migration-076 leak. 076 defaulted every document
to visible and tried to subtract exceptions; here documents default to
``private`` and only an explicit, owner-authorized flip to ``org`` widens them.
The tenant boundary is the organization boundary, so org-read is scoped to
``tenant_id`` — it never leaks across tenants.

The RLS tests run against the live schema (skipped without the isolated test DB).
"""

from __future__ import annotations

import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION = _ROOT / "db" / "migrations" / "110_org_corpus.sql"


def test_migration_110_adds_visibility_and_an_org_read_branch() -> None:
    migration = _MIGRATION.read_text(encoding="utf-8")
    assert "visibility" in migration
    assert "docray_readable_documents" in migration
    assert "'org'" in migration and "'private'" in migration
    assert "docray_set_document_visibility" in migration
    # The org branch is tenant-scoped, and must NOT reintroduce the blanket
    # role-read shape 076 leaked through (the guard in test_reader_visibility.py
    # enforces the same rule).
    assert "p.action = 'read'" not in migration


def _apply(conn) -> None:
    for name in (
        "102_document_ownership.sql",
        "104_member_role.sql",
        "106_force_rls.sql",
        "108_document_sharing.sql",
        "110_org_corpus.sql",
    ):
        conn.execute("RESET ROLE")
        conn.execute((_ROOT / "db" / "migrations" / name).read_text(encoding="utf-8"))
    conn.execute("RESET ROLE")


def _member(conn, label: str, *, tenant: str | None = None) -> str:
    tenant_expr = "%s::uuid" if tenant else "docray_default_tenant_id()"
    params: tuple = (f"{label}-{uuid.uuid4()}", tenant) if tenant else (f"{label}-{uuid.uuid4()}",)
    row = conn.execute(
        f"INSERT INTO principals (kind, display_name, tenant_id) "
        f"VALUES ('human', %s, {tenant_expr}) RETURNING id",
        params,
    ).fetchone()
    principal_id = str(row["id"])
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) "
        "SELECT %s, id FROM roles WHERE name = 'member' ON CONFLICT DO NOTHING",
        (principal_id,),
    )
    return principal_id


def _other_tenant(conn) -> str:
    row = conn.execute(
        "INSERT INTO tenants (slug, display_name) VALUES (%s, %s) RETURNING id",
        (f"org-{uuid.uuid4().hex[:8]}", "Other Org"),
    ).fetchone()
    return str(row["id"])


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


def test_org_visible_document_is_readable_by_a_tenant_member(pg) -> None:
    import _itdb

    conn, created = pg
    _apply(conn)
    doc_id = _itdb.make_doc(conn, "Published to the org.")
    created.append(doc_id)
    owner = _member(conn, "org-owner")
    colleague = _member(conn, "org-colleague")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    try:
        assert not _visible(conn, colleague, doc_id), "private by default"
        _as(conn, owner)
        conn.execute("SELECT docray_set_document_visibility(%s, 'org')", (doc_id,))
        _reset(conn)
        assert _visible(conn, colleague, doc_id), "org visibility reaches a tenant member"
    finally:
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, colleague))
        conn.execute("RESET ROLE")


def test_org_visibility_never_crosses_tenants(pg) -> None:
    import _itdb

    conn, created = pg
    _apply(conn)
    doc_id = _itdb.make_doc(conn, "Org doc stays inside its org.")
    created.append(doc_id)
    owner = _member(conn, "t1-owner")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    other_tenant = _other_tenant(conn)
    outsider = _member(conn, "t2-outsider", tenant=other_tenant)
    try:
        _as(conn, owner)
        conn.execute("SELECT docray_set_document_visibility(%s, 'org')", (doc_id,))
        _reset(conn)
        assert not _visible(conn, outsider, doc_id), "a different tenant must not see the org doc"
    finally:
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, outsider))
        conn.execute("DELETE FROM tenants WHERE id = %s", (other_tenant,))
        conn.execute("RESET ROLE")


def test_only_owner_or_admin_can_publish_to_the_org(pg) -> None:
    import _itdb
    import psycopg

    conn, created = pg
    _apply(conn)
    doc_id = _itdb.make_doc(conn, "Not yours to publish.")
    created.append(doc_id)
    owner = _member(conn, "pub-owner")
    stranger = _member(conn, "pub-stranger")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    try:
        _as(conn, stranger)
        raised = False
        try:
            conn.execute("SELECT docray_set_document_visibility(%s, 'org')", (doc_id,))
        except psycopg.errors.RaiseException:
            raised = True
        _reset(conn)
        assert raised, "a non-owner must not be able to publish another's document"
        # And it stayed private.
        colleague = _member(conn, "pub-colleague")
        assert not _visible(conn, colleague, doc_id)
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id = %s", (colleague,))
        conn.execute("RESET ROLE")
    finally:
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, stranger))
        conn.execute("RESET ROLE")


def test_visibility_can_be_set_back_to_private(pg) -> None:
    import _itdb

    conn, created = pg
    _apply(conn)
    doc_id = _itdb.make_doc(conn, "Unpublish me.")
    created.append(doc_id)
    owner = _member(conn, "unpub-owner")
    colleague = _member(conn, "unpub-colleague")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    try:
        _as(conn, owner)
        conn.execute("SELECT docray_set_document_visibility(%s, 'org')", (doc_id,))
        _reset(conn)
        assert _visible(conn, colleague, doc_id)
        _as(conn, owner)
        conn.execute("SELECT docray_set_document_visibility(%s, 'private')", (doc_id,))
        _reset(conn)
        assert not _visible(conn, colleague, doc_id), "back to private hides it again"
    finally:
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, colleague))
        conn.execute("RESET ROLE")


def test_invalid_visibility_value_is_rejected(pg) -> None:
    import _itdb
    import psycopg

    conn, created = pg
    _apply(conn)
    doc_id = _itdb.make_doc(conn, "Bad visibility.")
    created.append(doc_id)
    owner = _member(conn, "bad-owner")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    try:
        _as(conn, owner)
        raised = False
        try:
            conn.execute("SELECT docray_set_document_visibility(%s, 'public')", (doc_id,))
        except psycopg.errors.RaiseException:
            raised = True
        _reset(conn)
        assert raised, "only private/org are valid visibilities"
    finally:
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id = %s", (owner,))
        conn.execute("RESET ROLE")
