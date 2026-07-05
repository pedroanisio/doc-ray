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
