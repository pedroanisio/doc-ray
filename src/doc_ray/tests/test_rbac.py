"""Database-authenticated principal isolation tests."""

from __future__ import annotations

from pathlib import Path

from psycopg import sql

from doc_ray.domain.ulid import new_ulid_str

_ROOT = Path(__file__).resolve().parents[3]


def test_direct_client_role_cannot_impersonate_principal_with_guc(pg):
    """Hostile direct SQL as docray_client cannot choose identity via app.principal_id."""
    import _itdb

    conn, created = pg
    conn.execute((_ROOT / "db/migrations/030_principal_login_roles.sql").read_text())
    doc_a = _itdb.make_doc(conn, "Visible to principal A.")
    doc_b = _itdb.make_doc(conn, "Visible to principal B.")
    created.extend([doc_a, doc_b])
    role_a = f"docray_it_{new_ulid_str()[-10:].lower()}"
    principal_a = conn.execute(
        "INSERT INTO principals (kind, display_name) VALUES ('human', %s) RETURNING id",
        (f"principal-{role_a}",),
    ).fetchone()["id"]
    principal_b = conn.execute(
        "INSERT INTO principals (kind, display_name) VALUES ('human', %s) RETURNING id",
        (f"other-{role_a}",),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO document_acl (document_id, principal_id, permission) "
        "VALUES (%s,%s,'read'), (%s,%s,'read')",
        (doc_a, principal_a, doc_b, principal_b),
    )

    try:
        conn.execute("SELECT docray_create_principal_login(%s, %s)", (principal_a, role_a))

        conn.execute("SET ROLE docray_client")
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (str(principal_b),))
        assert conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"] == 0
        conn.execute("RESET ROLE")

        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role_a)))
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (str(principal_b),))
        rows = conn.execute("SELECT id FROM documents ORDER BY id").fetchall()
        assert [str(row["id"]) for row in rows] == [doc_a]
        conn.execute("RESET ROLE")

        assert conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"] >= 2
    finally:
        conn.execute("RESET ROLE")
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (principal_a, principal_b))
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_a)))


def test_docray_api_role_resolves_principal_from_guc(pg):
    """The webapp's per-user RLS depends on docray_api honouring app.principal_id.

    ``docray_current_principal()`` resolves ``app.principal_id`` ONLY for the
    trusted ``docray_api`` role. The webapp must therefore run
    ``DOCRAY_DB_ROLE=docray_api`` so a logged-in user's stamped principal is
    resolved and RLS grants that user their own documents. ``docray_client``
    deliberately ignores the GUC (see
    ``test_direct_client_role_cannot_impersonate_principal_with_guc``), so
    running the webapp as ``docray_client`` silently yields an empty corpus —
    this test locks the contrast that makes ``docray_api`` the required role.
    """
    import _itdb

    conn, created = pg
    doc = _itdb.make_doc(conn, "Readable only by the granted principal.")
    created.append(doc)
    principal = conn.execute(
        "INSERT INTO principals (kind, display_name) VALUES ('human', %s) RETURNING id",
        (f"api-princ-{new_ulid_str()[-10:].lower()}",),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO document_acl (document_id, principal_id, permission) VALUES (%s,%s,'read')",
        (doc, principal),
    )
    try:
        conn.execute("SET ROLE docray_api")
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (str(principal),))
        api_visible = conn.execute(
            "SELECT count(*) AS n FROM documents WHERE id = %s", (doc,)
        ).fetchone()["n"]
        conn.execute("SELECT set_config('app.principal_id', '', false)")
        conn.execute("RESET ROLE")

        conn.execute("SET ROLE docray_client")
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (str(principal),))
        client_visible = conn.execute(
            "SELECT count(*) AS n FROM documents WHERE id = %s", (doc,)
        ).fetchone()["n"]
    finally:
        conn.execute("SELECT set_config('app.principal_id', '', false)")
        conn.execute("RESET ROLE")
        conn.execute("DELETE FROM principals WHERE id = %s", (principal,))

    assert api_visible == 1, "docray_api must resolve app.principal_id (webapp per-user RLS)"
    assert client_visible == 0, "docray_client must NOT resolve app.principal_id"


def test_tenant_partition_blocks_cross_tenant_acl_misconfiguration(pg):
    """A tenant-A principal cannot read tenant-B docs even with an erroneous ACL."""
    import _itdb

    conn, created = pg
    conn.execute((_ROOT / "db/migrations/061_tenant_foundation.sql").read_text())
    tenant_a = conn.execute(
        "INSERT INTO tenants (slug, display_name) VALUES (%s, %s) "
        "ON CONFLICT (slug) DO UPDATE SET display_name = EXCLUDED.display_name "
        "RETURNING id",
        ("tenant-a-" + new_ulid_str().lower(), "Tenant A"),
    ).fetchone()["id"]
    tenant_b = conn.execute(
        "INSERT INTO tenants (slug, display_name) VALUES (%s, %s) "
        "ON CONFLICT (slug) DO UPDATE SET display_name = EXCLUDED.display_name "
        "RETURNING id",
        ("tenant-b-" + new_ulid_str().lower(), "Tenant B"),
    ).fetchone()["id"]
    doc_a = _itdb.make_doc(conn, "Visible to tenant A.")
    doc_b = _itdb.make_doc(conn, "Hidden tenant B.")
    created.extend([doc_a, doc_b])
    conn.execute("UPDATE documents SET tenant_id = %s WHERE id = %s", (tenant_a, doc_a))
    conn.execute("UPDATE documents SET tenant_id = %s WHERE id = %s", (tenant_b, doc_b))
    role_a = f"docray_tenant_{new_ulid_str()[-10:].lower()}"
    principal_a = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) VALUES ('human', %s, %s) "
        "RETURNING id",
        (f"tenant-principal-{role_a}", tenant_a),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO document_acl (document_id, principal_id, permission) "
        "VALUES (%s,%s,'read'), (%s,%s,'read')",
        (doc_a, principal_a, doc_b, principal_a),
    )

    try:
        conn.execute("SELECT docray_create_principal_login(%s, %s)", (principal_a, role_a))
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role_a)))
        rows = conn.execute("SELECT id FROM documents ORDER BY id").fetchall()

        assert [str(row["id"]) for row in rows] == [doc_a]
    finally:
        conn.execute("RESET ROLE")
        conn.execute("DELETE FROM principals WHERE id = %s", (principal_a,))
        conn.execute("DELETE FROM documents WHERE id IN (%s, %s)", (doc_a, doc_b))
        conn.execute("DELETE FROM tenants WHERE id IN (%s, %s)", (tenant_a, tenant_b))
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_a)))
