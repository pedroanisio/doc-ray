"""GraphQL briefing query over request-scoped, RLS-enforced read adapters."""

from __future__ import annotations

import asyncio
from typing import Any

import anyio
import strawberry
from psycopg import AsyncConnection

from doc_ray.adapters import persistence_read as read
from doc_ray.adapters import persistence_semantic_read as semantic_read
from doc_ray.adapters.text_encoder_sentence_transformers import SemanticStackUnavailableError
from doc_ray.application.briefing import BriefingReader, SemanticUnavailable, build_briefing_pack
from doc_ray.entrypoints.api.briefing_types import BriefingPack
from doc_ray.entrypoints.api.types import Info


class _GraphQLBriefingReader(BriefingReader):
    """Normalize persistence rows without weakening the request's RLS boundary.

    All reads run on ONE request-scoped async connection, which cannot service
    concurrent operations. The shared briefing service fans reads out with
    ``asyncio.gather`` (discovery + probe; per-document passages + entities), so a
    lock serializes this reader's connection access — correctness over the
    parallelism a single psycopg connection cannot provide anyway. The MCP reader
    needs no such lock: it opens a fresh HTTP client per call.
    """

    def __init__(self, conn: AsyncConnection[dict[str, Any]]) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()

    async def full_text_search(self, topic: str, *, limit: int) -> list[dict[str, Any]]:
        async with self._lock:
            rows = await read.fulltext_search(self._conn, topic, limit=limit)
        return [
            {"documentId": str(row["doc_id"]), "title": row.get("title"), "rank": row.get("rank")}
            for row in rows
        ]

    async def search_sentences(
        self, term: str, *, document_id: str, limit: int
    ) -> list[dict[str, Any]]:
        async with self._lock:
            rows = await read.search_sentences(
                self._conn, term, document_id=document_id, limit=limit
            )
        return [
            {
                "id": str(row["id"]),
                "documentId": str(row["doc_id"]),
                "title": row.get("title"),
                "ordinal": row.get("ordinal"),
                "text": row.get("text"),
                "pre": row.get("pre", ""),
                "hit": row.get("hit", ""),
                "post": row.get("post", ""),
            }
            for row in rows
        ]

    async def entity_frequency(self, *, document_id: str, limit: int) -> list[dict[str, Any]]:
        async with self._lock:
            rows = await read.entity_frequency(self._conn, document_id=document_id, limit=limit)
        return [
            {
                "text": row.get("text"),
                "entityType": row.get("entity_type"),
                "count": row.get("count"),
            }
            for row in rows
        ]

    async def semantic_search_sentences(
        self, query: str, *, document_id: str | None = None, limit: int
    ) -> list[dict[str, Any]]:
        # Reuse the semantic field's cached, active encoder — one instance, no
        # second ranking implementation. Embedding runs off the event loop.
        from doc_ray.application.semantic_embed import to_vector_literal
        from doc_ray.entrypoints.api.semantic_query import _query_text_encoder

        try:
            encoder = _query_text_encoder()
        except SemanticStackUnavailableError as exc:
            raise SemanticUnavailable(str(exc)) from exc
        vectors = await anyio.to_thread.run_sync(encoder.embed_texts, [query])
        query_vector = to_vector_literal(vectors[0])
        async with self._lock:
            rows = await semantic_read.semantic_search_sentences(
                self._conn,
                query_vector,
                encoder.model_id,
                document_id=document_id,
                limit=limit,
            )
        return [
            {
                "id": str(row["sentence_id"]),
                "documentId": str(row["document_id"]),
                "ordinal": row.get("ordinal"),
                "text": row.get("text"),
            }
            for row in rows
        ]


@strawberry.type
class BriefingQueries:
    """Cited evidence composition mixed into the GraphQL ``Query`` root."""

    @strawberry.field
    async def briefing_pack(
        self,
        info: Info,
        topic: str,
        max_documents: int = 6,
        passages_per_document: int = 4,
        max_entities: int = 25,
    ) -> BriefingPack:
        """Assemble a gap-honest evidence pack from this principal's visible corpus."""
        ctx = info.context
        pack = await build_briefing_pack(
            topic,
            reader=_GraphQLBriefingReader(ctx.conn),
            max_documents=ctx.page_size(max_documents),
            passages_per_document=ctx.page_size(passages_per_document),
            max_entities=ctx.page_size(max_entities),
        )
        return BriefingPack.from_dict(pack)
