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


def _seed(pg) -> str:
    import _itdb

    conn, created = pg
    conn.execute((_MIGRATIONS / "020_jobs.sql").read_text())  # ensure jobs table
    doc_id = _itdb.make_doc(conn, "Reannotate me. Second sentence.")
    created.append(doc_id)
    return doc_id


def test_reannotate_and_delete_enqueue(pg):
    doc_id = _seed(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn()
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
