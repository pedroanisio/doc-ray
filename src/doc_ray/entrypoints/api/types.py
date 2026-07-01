"""Strawberry GraphQL types over the document store.

Types carry the scalar columns; relationships (authors, tags, metadata, sentences,
provenance, lexeme occurrences) are resolved lazily — the by-document ones through
per-request DataLoaders, so listing documents and selecting their satellites stays
one batched query per relationship rather than N. Class and field docstrings double
as the GraphQL schema descriptions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import strawberry
from strawberry.scalars import JSON

from doc_ray.adapters import persistence_read as read
from doc_ray.entrypoints.api.context import RequestContext

#: Resolver info parametrised with our context (satisfies mypy disallow-any-generics).
Info = strawberry.Info[RequestContext, None]
Row = dict[str, Any]


@strawberry.type
class MetadataEntry:
    """One document metadata entry."""

    key: str
    value: str


@strawberry.type
class Sentence:
    """A single segmented sentence."""

    id: strawberry.ID
    ordinal: int
    text: str
    char_start: int
    char_end: int

    @classmethod
    def from_row(cls, row: Row) -> Sentence:
        """Build a ``Sentence`` from a read-adapter row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            ordinal=row["ordinal"],
            text=row["text"],
            char_start=row["char_start"],
            char_end=row["char_end"],
        )


@strawberry.type
class ProvenanceEvent:
    """A provenance event with its reassembled parameters."""

    id: strawberry.ID
    kind: str
    tool: str | None
    tool_version: str | None
    occurred_at: datetime
    actor: str | None
    note: str | None
    params: JSON

    @classmethod
    def from_row(cls, row: Row) -> ProvenanceEvent:
        """Build a ``ProvenanceEvent`` from a read-adapter row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            kind=row["kind"],
            tool=row.get("tool"),
            tool_version=row.get("tool_version"),
            occurred_at=row["occurred_at"],
            actor=row.get("actor"),
            note=row.get("note"),
            params=cast(JSON, row.get("params") or {}),
        )


@strawberry.type
class Document:
    """A document; relationships resolve lazily (batched where by-document)."""

    id: strawberry.ID
    title: str
    doc_type: str
    mime_type: str | None
    language: str | None
    status: str
    error: str | None
    page_count: int | None
    char_count: int | None
    byte_size: int | None
    content_sha256: str | None
    source_uri: str | None
    original_filename: str | None
    created_at: datetime
    updated_at: datetime | None

    @classmethod
    def from_row(cls, row: Row) -> Document:
        """Build a ``Document`` from a summary or full read-adapter row.

        Detail-only columns absent from a list-summary row resolve to ``None``.
        """
        return cls(
            id=strawberry.ID(str(row["id"])),
            title=row["title"],
            doc_type=row["doc_type"],
            mime_type=row.get("mime_type"),
            language=row.get("language"),
            status=row["status"],
            error=row.get("error"),
            page_count=row.get("page_count"),
            char_count=row.get("char_count"),
            byte_size=row.get("byte_size"),
            content_sha256=row.get("content_sha256"),
            source_uri=row.get("source_uri"),
            original_filename=row.get("original_filename") or row.get("filename"),
            created_at=row["created_at"],
            updated_at=row.get("updated_at"),
        )

    @strawberry.field
    async def authors(self, info: Info) -> list[str]:
        """Resolve the document's authors via the batched loader."""
        return await info.context.loaders.authors.load(str(self.id))

    @strawberry.field
    async def tags(self, info: Info) -> list[str]:
        """Resolve the document's tags via the batched loader."""
        return await info.context.loaders.tags.load(str(self.id))

    @strawberry.field
    async def metadata(self, info: Info) -> list[MetadataEntry]:
        """Resolve the document's metadata via the batched loader."""
        data = await info.context.loaders.metadata.load(str(self.id))
        return [MetadataEntry(key=k, value=v) for k, v in data.items()]

    @strawberry.field
    async def sentence_count(self, info: Info) -> int:
        """Resolve the document's sentence count via the batched loader."""
        return await info.context.loaders.sentence_count.load(str(self.id))

    @strawberry.field
    async def sentences(self, info: Info, limit: int = 40, offset: int = 0) -> list[Sentence]:
        """Resolve a bounded page of the document's sentences."""
        ctx = info.context
        rows = await read.list_sentences(
            ctx.conn, str(self.id), limit=ctx.page_size(limit), offset=offset
        )
        return [Sentence.from_row(r) for r in rows]

    @strawberry.field
    async def provenance(self, info: Info) -> list[ProvenanceEvent]:
        """Resolve the document's provenance trail."""
        rows = await read.get_provenance(info.context.conn, str(self.id))
        return [ProvenanceEvent.from_row(r) for r in rows]


@strawberry.type
class LexemeOccurrence:
    """One occurrence with a highlightable pre/hit/post split."""

    document_id: strawberry.ID
    title: str
    char_start: int
    surface: str
    kwic: str
    pre: str
    hit: str
    post: str

    @classmethod
    def from_row(cls, row: Row) -> LexemeOccurrence:
        """Build a ``LexemeOccurrence`` from a read-adapter row."""
        return cls(
            document_id=strawberry.ID(str(row["doc_id"])),
            title=row["title"],
            char_start=row["char_start"],
            surface=row.get("surface") or "",
            kwic=row.get("kwic") or "",
            pre=row.get("pre") or "",
            hit=row.get("hit") or "",
            post=row.get("post") or "",
        )


@strawberry.type
class Lexeme:
    """A lexeme; its occurrences resolve lazily as keyword-in-context."""

    uid: strawberry.ID
    lang: str
    pos: str
    lemma: str
    uses: int | None

    @classmethod
    def from_row(cls, row: Row) -> Lexeme:
        """Build a ``Lexeme`` from a read-adapter row."""
        return cls(
            uid=strawberry.ID(str(row["uid"])),
            lang=row["lang"],
            pos=row["pos"],
            lemma=row["lemma"],
            uses=row.get("uses"),
        )

    @strawberry.field
    async def occurrences(self, info: Info, limit: int = 80) -> list[LexemeOccurrence]:
        """Resolve a bounded page of this lexeme's occurrences."""
        ctx = info.context
        rows = await read.lexeme_kwic(ctx.conn, str(self.uid), limit=ctx.page_size(limit))
        return [LexemeOccurrence.from_row(r) for r in rows]


@strawberry.type
class SentenceHit:
    """A sentence search hit with a highlightable pre/hit/post split."""

    document_id: strawberry.ID
    title: str
    ordinal: int
    text: str
    pre: str
    hit: str
    post: str

    @classmethod
    def from_row(cls, row: Row) -> SentenceHit:
        """Build a ``SentenceHit`` from a read-adapter row."""
        return cls(
            document_id=strawberry.ID(str(row["doc_id"])),
            title=row["title"],
            ordinal=row["ordinal"],
            text=row["text"],
            pre=row.get("pre") or "",
            hit=row.get("hit") or "",
            post=row.get("post") or "",
        )


@strawberry.type
class DocumentHit:
    """A full-text search hit with its rank."""

    document_id: strawberry.ID
    title: str
    rank: float

    @classmethod
    def from_row(cls, row: Row) -> DocumentHit:
        """Build a ``DocumentHit`` from a read-adapter row."""
        return cls(
            document_id=strawberry.ID(str(row["doc_id"])),
            title=row["title"],
            rank=float(row["rank"]),
        )
