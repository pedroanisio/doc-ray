"""GraphQL document full-text search query surface."""

from __future__ import annotations

from enum import Enum

import strawberry

from doc_ray.adapters import persistence_fulltext_read as fulltext_read
from doc_ray.entrypoints.api.types import DocumentHit, Info


@strawberry.enum
class FullTextSearchMode(Enum):
    """Safe parsing semantics for document-level full-text search."""

    TERMS = "terms"
    PHRASE = "phrase"


@strawberry.type
class FullTextQueries:
    """Document-level language-aware search fields."""

    @strawberry.field
    async def full_text_search(
        self,
        info: Info,
        query: str,
        limit: int = 25,
        mode: FullTextSearchMode = FullTextSearchMode.TERMS,
    ) -> list[DocumentHit]:
        """Return ranked document hits using all-term or ordered-phrase matching."""
        rows = await fulltext_read.fulltext_search(
            info.context.conn,
            query,
            limit=info.context.page_size(limit),
            mode=mode.value,
        )
        return [DocumentHit.from_row(row) for row in rows]
