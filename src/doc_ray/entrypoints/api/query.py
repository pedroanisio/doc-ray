"""The GraphQL ``Query`` root — read-only entry points over the store.

Every resolver is thin: it clamps its page size and delegates to the async read
adapter (:mod:`doc_ray.adapters.persistence_read`), then maps rows to types. No SQL
lives here.
"""

from __future__ import annotations

import strawberry

from doc_ray.adapters import persistence_read as read
from doc_ray.adapters import queue_postgres as queue
from doc_ray.adapters import seed_suggestion_store as seeds
from doc_ray.entrypoints.api.types import (
    Document,
    DocumentHit,
    EntityFrequency,
    EntityMention,
    Info,
    Job,
    Lexeme,
    SeedSuggestion,
    SentenceHit,
)


@strawberry.type
class Query:
    """The GraphQL query root."""

    @strawberry.field
    def version(self) -> str:
        """Return the running service's semantic version (``doc_ray.__version__``)."""
        from doc_ray import __version__

        return __version__

    @strawberry.field
    async def documents(self, info: Info, limit: int = 50, offset: int = 0) -> list[Document]:
        """Return a bounded page of document summaries."""
        ctx = info.context
        rows = await read.list_documents(ctx.conn, limit=ctx.page_size(limit), offset=offset)
        return [Document.from_row(r) for r in rows]

    @strawberry.field
    async def document(self, info: Info, id: strawberry.ID) -> Document | None:
        """Return the document with *id*, or ``None`` if it does not exist."""
        row = await read.get_document(info.context.conn, str(id))
        return Document.from_row(row) if row is not None else None

    @strawberry.field
    async def search_sentences(
        self,
        info: Info,
        term: str,
        document_id: strawberry.ID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SentenceHit]:
        """Return sentence hits for *term* (bounded page).

        Pass ``documentId`` to search within one document, and ``offset`` to
        enumerate all matches for a common term page by page.
        """
        ctx = info.context
        rows = await read.search_sentences(
            ctx.conn,
            term,
            document_id=str(document_id) if document_id is not None else None,
            limit=ctx.page_size(limit),
            offset=offset,
        )
        return [SentenceHit.from_row(r) for r in rows]

    @strawberry.field
    async def full_text_search(self, info: Info, query: str, limit: int = 25) -> list[DocumentHit]:
        """Return full-text document hits for *query* (bounded page)."""
        ctx = info.context
        rows = await read.fulltext_search(ctx.conn, query, limit=ctx.page_size(limit))
        return [DocumentHit.from_row(r) for r in rows]

    @strawberry.field
    async def lexeme(self, info: Info, uid: strawberry.ID) -> Lexeme | None:
        """Return the lexeme with *uid*, or ``None`` if it does not exist."""
        row = await read.get_lexeme(info.context.conn, str(uid))
        return Lexeme.from_row(row) if row is not None else None

    @strawberry.field
    async def lexemes(
        self,
        info: Info,
        q: str | None = None,
        pos: str | None = None,
        lang: str | None = None,
        document_id: strawberry.ID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Lexeme]:
        """Return a bounded page of filtered lexemes.

        With ``documentId``, only lexemes occurring in that document are returned
        and ``uses`` counts occurrences there — a per-document vocabulary table
        instead of corpus-global totals.
        """
        ctx = info.context
        rows = await read.list_lexemes(
            ctx.conn,
            q=q,
            pos=pos,
            lang=lang,
            document_id=str(document_id) if document_id is not None else None,
            limit=ctx.page_size(limit),
            offset=offset,
        )
        return [Lexeme.from_row(r) for r in rows]

    @strawberry.field
    async def entities(
        self,
        info: Info,
        document_id: strawberry.ID | None = None,
        entity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EntityMention]:
        """Return named-entity mentions (NER layer), filterable by document and type."""
        ctx = info.context
        rows = await read.list_entities(
            ctx.conn,
            document_id=str(document_id) if document_id is not None else None,
            entity_type=entity_type,
            limit=ctx.page_size(limit),
            offset=offset,
        )
        return [EntityMention.from_row(r) for r in rows]

    @strawberry.field
    async def entity_frequency(
        self,
        info: Info,
        document_id: strawberry.ID | None = None,
        entity_type: str | None = None,
        limit: int = 100,
    ) -> list[EntityFrequency]:
        """Return entity mention frequencies — the character/place/org table."""
        ctx = info.context
        rows = await read.entity_frequency(
            ctx.conn,
            document_id=str(document_id) if document_id is not None else None,
            entity_type=entity_type,
            limit=ctx.page_size(limit),
        )
        return [EntityFrequency.from_row(r) for r in rows]

    @strawberry.field
    async def job(self, info: Info, id: strawberry.ID) -> Job | None:
        """Return a background job's status by id, or ``None`` if unknown."""
        row = await queue.status_async(info.context.conn, str(id))
        return Job.from_row(row) if row is not None else None

    @strawberry.field
    async def seed_suggestions(
        self, info: Info, document_id: strawberry.ID, status: str | None = None
    ) -> list[SeedSuggestion]:
        """Return a document's seed suggestions (unresolved last, then by frequency)."""
        rows = await seeds.list_for_document_async(
            info.context.conn, str(document_id), status=status
        )
        return [SeedSuggestion.from_row(r) for r in rows]
