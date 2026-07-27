"""GraphQL sentence search fields, split from the query root for D-015."""

from __future__ import annotations

from enum import Enum

import strawberry

from doc_ray.adapters import persistence_read as read
from doc_ray.entrypoints.api.ids import clean_uuid
from doc_ray.entrypoints.api.sentence_search_types import SentenceHit, SentenceSearchPage
from doc_ray.entrypoints.api.types import Info, Row


@strawberry.enum
class SentenceMatchMode(Enum):
    """Sentence-level match rule; substring remains the compatibility default."""

    SUBSTRING = "substring"
    PHRASE = "phrase"


async def _rows(
    info: Info,
    term: str,
    document_id: strawberry.ID | None,
    limit: int,
    after: str | None,
    match: SentenceMatchMode,
) -> list[Row]:
    """Validate scope and delegate both public fields to the same read."""
    doc_id: str | None = None
    if document_id is not None:
        doc_id = clean_uuid(str(document_id))
        if doc_id is None:
            return []
    return await read.search_sentences(
        info.context.conn,
        term,
        document_id=doc_id,
        limit=info.context.page_size(limit),
        after=after,
        match=match.value,
    )


@strawberry.type
class SentenceSearchQueries:
    """Legacy list and metadata-bearing sentence search surfaces."""

    @strawberry.field
    async def search_sentences(
        self,
        info: Info,
        term: str,
        document_id: strawberry.ID | None = None,
        limit: int = 100,
        after: str | None = None,
        match: SentenceMatchMode = SentenceMatchMode.SUBSTRING,
    ) -> list[SentenceHit]:
        """Return a bounded page; phrase mode adds verified occurrence citations."""
        rows = await _rows(info, term, document_id, limit, after, match)
        return [SentenceHit.from_row(row) for row in rows]

    @strawberry.field
    async def search_sentence_page(
        self,
        info: Info,
        term: str,
        document_id: strawberry.ID | None = None,
        limit: int = 100,
        after: str | None = None,
        match: SentenceMatchMode = SentenceMatchMode.SUBSTRING,
    ) -> SentenceSearchPage:
        """Return hits with phrase candidate-scan completeness evidence."""
        rows = await _rows(info, term, document_id, limit, after, match)
        return SentenceSearchPage.from_rows(rows, phrase=match is SentenceMatchMode.PHRASE)
