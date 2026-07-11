"""GraphQL mutation tests: enqueue jobs via schema.execute (skip without a DB).

Seeds a document (sync), ensures the jobs table, then runs mutations against an async
connection and checks a queued job is created and readable via the job query. Jobs are
cleaned up by cascade when the seeded document is deleted in teardown.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from doc_ray.domain.ulid import new_ulid_str
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import Principal

_MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"
WRITE_PRINCIPAL = Principal(
    subject="test-writer",
    principal_id="00000000-0000-0000-0000-000000000001",
    scopes=frozenset({"read", "write"}),
)


async def _aconn() -> psycopg.AsyncConnection:
    import _itdb

    return await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )


async def _aconn_as(principal_id: str | None) -> psycopg.AsyncConnection:
    """An async connection scoped to docray_api, optionally as *principal_id*.

    Mirrors what entrypoints/api/security.py's apply_principal does per
    request — SET ROLE docray_api, then stamp app.principal_id — so RLS
    actually applies, unlike the plain superuser connection _aconn() returns.
    """
    import _itdb

    conn = await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )
    await conn.execute("SET ROLE docray_api")
    await conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id or "",))
    return conn


def _seed(pg) -> str:
    import _itdb

    conn, created = pg
    conn.execute((_MIGRATIONS / "020_jobs.sql").read_text())  # ensure jobs table
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer")
    doc_id = _itdb.make_doc(conn, "Reannotate me. Second sentence.")
    created.append(doc_id)
    return doc_id


def test_reannotate_and_delete_enqueue(pg):
    doc_id = _seed(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            reann = await schema.execute(
                'mutation ($id: ID!) { reannotate(documentId: $id, lang: "en") '
                "{ id kind status documentId } }",
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert reann.errors is None, reann.errors
            job = reann.data["reannotate"]
            assert job["kind"] == "reannotate"
            assert job["status"] == "queued"
            assert job["documentId"] == doc_id

            # The job query reads it back.
            polled = await schema.execute(
                "query ($id: ID!) { job(id: $id) { kind status } }",
                variable_values={"id": job["id"]},
                context_value=ctx,
            )
            assert polled.errors is None, polled.errors
            assert polled.data["job"]["kind"] == "reannotate"

            deletion = await schema.execute(
                "mutation ($id: ID!) { deleteDocument(documentId: $id) { kind status } }",
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert deletion.errors is None, deletion.errors
            assert deletion.data["deleteDocument"]["kind"] == "delete"
            assert deletion.data["deleteDocument"]["status"] == "queued"
        finally:
            await aconn.close()

    asyncio.run(check())


# ---------------------------------------------------------------------------
# BL-064 — a global "write" scope must not let a principal mutate a document
# outside their own document_acl grants; jobs/backups must enforce this at
# the RLS layer too, not just in the resolver.
# ---------------------------------------------------------------------------


def _make_principal(conn, *, display_name: str) -> str:
    return conn.execute(
        "INSERT INTO principals (kind, display_name) VALUES ('human', %s) RETURNING id",
        (display_name,),
    ).fetchone()["id"]


def test_reannotate_rejects_write_scope_without_document_grant(pg):
    """Global write scope alone must not authorize mutating an arbitrary document."""
    conn, created = pg
    doc_id = _seed(pg)
    outsider = _make_principal(conn, display_name=f"outsider-{new_ulid_str()}")
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(str(outsider))
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=Principal(
                    subject="outsider",
                    principal_id=str(outsider),
                    scopes=frozenset({"read", "write"}),
                ),
                loaders=Loaders(aconn),
            )
            result = await schema.execute(
                'mutation ($id: ID!) { reannotate(documentId: $id, lang: "en") { id } }',
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert result.errors is not None
            assert "write grant" in str(result.errors[0]).lower()
        finally:
            await aconn.close()

    asyncio.run(check())
    # No job was actually enqueued for this document by the rejected principal.
    assert (
        conn.execute("SELECT count(*) AS n FROM jobs WHERE document_id = %s", (doc_id,)).fetchone()[
            "n"
        ]
        == 0
    )
    conn.execute("DELETE FROM principals WHERE id = %s", (outsider,))


def test_reannotate_allows_write_scope_with_document_grant(pg):
    """A principal holding an explicit document_acl grant may mutate that document."""
    conn, created = pg
    doc_id = _seed(pg)
    grantee = _make_principal(conn, display_name=f"grantee-{new_ulid_str()}")
    conn.execute(
        "INSERT INTO document_acl (document_id, principal_id, permission) VALUES (%s, %s, 'annotate')",
        (doc_id, grantee),
    )
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(str(grantee))
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=Principal(
                    subject="grantee",
                    principal_id=str(grantee),
                    scopes=frozenset({"read", "write"}),
                ),
                loaders=Loaders(aconn),
            )
            result = await schema.execute(
                'mutation ($id: ID!) { reannotate(documentId: $id, lang: "en") { kind status } }',
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["reannotate"]["status"] == "queued"
        finally:
            await aconn.close()

    asyncio.run(check())
    conn.execute("DELETE FROM principals WHERE id = %s", (grantee,))


def test_create_backup_requires_admin_not_just_write_scope(pg):
    """create_backup/restore_backup are corpus-wide — a non-admin write token must be refused."""
    conn, created = pg
    non_admin = _make_principal(conn, display_name=f"non-admin-{new_ulid_str()}")
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(str(non_admin))
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=Principal(
                    subject="non-admin",
                    principal_id=str(non_admin),
                    scopes=frozenset({"read", "write"}),
                ),
                loaders=Loaders(aconn),
            )
            result = await schema.execute(
                'mutation { createBackup(note: "x") { id } }',
                context_value=ctx,
            )
            assert result.errors is not None
            assert "admin" in str(result.errors[0]).lower()
        finally:
            await aconn.close()

    asyncio.run(check())
    conn.execute("DELETE FROM principals WHERE id = %s", (non_admin,))


def test_create_backup_allows_admin_principal(pg):
    conn, created = pg
    admin = conn.execute(
        "INSERT INTO principals (kind, display_name) VALUES ('human', %s) RETURNING id",
        (f"admin-{new_ulid_str()}",),
    ).fetchone()["id"]
    admin_role_id = conn.execute("SELECT id FROM roles WHERE name = 'admin'").fetchone()["id"]
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) VALUES (%s, %s)",
        (admin, admin_role_id),
    )
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(str(admin))
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=Principal(
                    subject="admin", principal_id=str(admin), scopes=frozenset({"read", "write"})
                ),
                loaders=Loaders(aconn),
            )
            result = await schema.execute(
                'mutation { createBackup(note: "x") { kind status } }',
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["createBackup"]["status"] == "queued"
        finally:
            await aconn.close()

    asyncio.run(check())
    conn.execute(
        "DELETE FROM jobs WHERE kind = 'backup' AND document_id IS NULL AND payload->>'note' = 'x'"
    )
    conn.execute("DELETE FROM principal_roles WHERE principal_id = %s", (admin,))
    conn.execute("DELETE FROM principals WHERE id = %s", (admin,))
