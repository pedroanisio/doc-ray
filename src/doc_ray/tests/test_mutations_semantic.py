"""GraphQL mutation test: embedDocumentSentences enqueues a semantic_embed job.

Mirrors test_mutations.py — seeds a document (sync), then runs the mutation
against an async, principal-scoped connection so RLS/write-scope apply. DB-backed
(skips cleanly without an isolated test DB).
"""

from __future__ import annotations

import asyncio

from _mutations_common import WRITE_PRINCIPAL, _aconn_as, _seed

from doc_ray.domain.jobs import KIND_SEMANTIC_EMBED
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema


def test_embed_document_sentences_enqueues_semantic_embed_job(pg) -> None:
    doc_id = _seed(pg)
    schema = build_schema()

    async def run() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            result = await schema.execute(
                "mutation ($id: ID!) { embedDocumentSentences(documentId: $id) "
                "{ id kind status documentId } }",
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            job = result.data["embedDocumentSentences"]
            assert job["kind"] == KIND_SEMANTIC_EMBED
            assert job["documentId"] == doc_id

            polled = await schema.execute(
                "query ($id: ID!) { job(id: $id) { kind status } }",
                variable_values={"id": job["id"]},
                context_value=ctx,
            )
            assert polled.data["job"]["kind"] == KIND_SEMANTIC_EMBED
        finally:
            await aconn.close()

    asyncio.run(run())
