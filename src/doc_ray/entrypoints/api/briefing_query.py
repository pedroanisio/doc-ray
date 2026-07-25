"""GraphQL briefing query over request-scoped, RLS-enforced read adapters."""

from __future__ import annotations

from typing import Any

import strawberry
from psycopg import AsyncConnection

from doc_ray.adapters import persistence_read as read
from doc_ray.application.briefing import BriefingReader, build_briefing_pack
from doc_ray.entrypoints.api.briefing_types import BriefingPack
from doc_ray.entrypoints.api.types import Info


class _GraphQLBriefingReader(BriefingReader):
    """Normalize persistence rows without weakening the request's RLS boundary."""

    def __init__(self, conn: AsyncConnection[dict[str, Any]]) -> None:
        self._conn = conn

    async def full_text_search(self, topic: str, *, limit: int) -> list[dict[str, Any]]:
        rows = await read.fulltext_search(self._conn, topic, limit=limit)
        return [
            {"documentId": str(row["doc_id"]), "title": row.get("title"), "rank": row.get("rank")}
            for row in rows
        ]

    async def search_sentences(
        self, term: str, *, document_id: str, limit: int
    ) -> list[dict[str, Any]]:
        rows = await read.search_sentences(self._conn, term, document_id=document_id, limit=limit)
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
        rows = await read.entity_frequency(self._conn, document_id=document_id, limit=limit)
        return [
            {
                "text": row.get("text"),
                "entityType": row.get("entity_type"),
                "count": row.get("count"),
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
