"""Shared fixtures/helpers for the GraphQL mutation test modules (not a test module).

Used by test_mutations.py and test_mutations_metadata.py: a seeded document
against the live Postgres, plus async connections that mirror the API's
per-request role/principal scoping so RLS actually applies.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.rows import dict_row

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
