"""updateDocumentMetadata / documentMetadataHistory mutation tests (BL-058, BL-110).

Split from test_mutations.py to keep both modules under the 400 net-LOC cap
(D-015); shares its seeded-document/async-connection helpers via
_mutations_common.py.
"""

from __future__ import annotations

import asyncio

from _mutations_common import WRITE_PRINCIPAL, _aconn, _aconn_as, _seed

from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema


def test_update_document_metadata_enqueues_and_history_reads_back(pg):
    """updateDocumentMetadata enqueues a job (BL-058); documentMetadataHistory reads it."""
    doc_id = _seed(pg)
    conn, _created = pg
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            result = await schema.execute(
                "mutation ($id: ID!) { updateDocumentMetadata(documentId: $id, "
                'title: "Renamed", tags: ["reviewed"]) { id kind status documentId } }',
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            job = result.data["updateDocumentMetadata"]
            assert job["kind"] == "update_metadata"
            assert job["status"] == "queued"
            assert job["documentId"] == doc_id

            # Apply synchronously (a worker would normally do this) so the
            # history read-surface has something to read back.
            from doc_ray.application.jobs import handle_update_metadata
            from doc_ray.domain.jobs import Job as DomainJob

            handle_update_metadata(
                conn,
                None,
                DomainJob(
                    id="0" * 26,
                    kind="update_metadata",
                    status="queued",
                    document_id=doc_id,
                    payload={"title": "Renamed", "tags": ["reviewed"]},
                ),
            )

            history = await schema.execute(
                "query ($id: ID!) { documentMetadataHistory(documentId: $id) "
                "{ field oldValue newValue } }",
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert history.errors is None, history.errors
            fields = {h["field"] for h in history.data["documentMetadataHistory"]}
            assert fields == {"title", "tags"}
        finally:
            await aconn.close()

    asyncio.run(check())


def test_update_document_metadata_rejects_malformed_id_without_leaking_postgres_error(pg):
    """A malformed documentId must fail as a clean grant error, never as raw
    Postgres uuid-syntax text, and must not enqueue a job (BL-110)."""
    _seed(pg)  # ensures the jobs table exists
    conn, _created = pg
    schema = build_schema()
    jobs_before = conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"]

    async def check():
        aconn = await _aconn()
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            result = await schema.execute(
                'mutation { updateDocumentMetadata(documentId: "not-a-uuid", title: "X") { id } }',
                context_value=ctx,
            )
            assert result.errors is not None
            message = str(result.errors[0])
            assert "invalid input syntax" not in message, message
            assert "write grant" in message.lower(), message
        finally:
            await aconn.close()

    asyncio.run(check())
    assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == jobs_before
