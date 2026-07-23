"""Strawberry GraphQL types for documents and their satellite relationships.

Split out of :mod:`doc_ray.entrypoints.api.types` (D-015), following the
``layer_types``/``canonical_types`` sibling precedent. Relationships resolve
lazily — the by-document ones through per-request DataLoaders, so listing
documents and selecting their satellites stays one batched query per
relationship rather than N. Class and field docstrings double as the GraphQL
schema descriptions.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast

import strawberry
from strawberry.scalars import JSON

from doc_ray.adapters import persistence_layers_read as layer_read
from doc_ray.adapters import persistence_read as read
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.layer_types import (
    AnnotationRun,
    Chunk,
    CorefChain,
    DependencyEdge,
    EntityLink,
    Token,
)

#: Resolver info parametrised with our context (satisfies mypy disallow-any-generics).
Info = strawberry.Info[RequestContext, None]
Row = dict[str, Any]


@strawberry.type
class DocumentCollection:
    """A manual or smart grouping of documents."""

    id: strawberry.ID
    name: str
    description: str
    kind: str
    rule: JSON
    rule_summary: str
    document_count: int
    created_at: datetime
    updated_at: datetime
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> DocumentCollection:
        """Build a ``DocumentCollection`` from a read-adapter row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            name=row["name"],
            description=row.get("description") or "",
            kind=row["kind"],
            rule=cast(JSON, row.get("rule") or {}),
            rule_summary=row.get("rule_summary") or "",
            document_count=int(row.get("document_count") or 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            cursor=row.get("cursor") or str(row["id"]),
        )

    @strawberry.field
    async def documents(
        self, info: Info, limit: int = 50, after: str | None = None
    ) -> list[Document]:
        """Return visible documents that currently belong to this collection."""
        rows = await read.list_collection_documents(
            info.context.conn,
            {
                "id": str(self.id),
                "kind": self.kind,
                "rule": self.rule,
            },
            limit=info.context.page_size(limit),
            after=after,
        )
        return [Document.from_row(row) for row in rows]


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
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> Sentence:
        """Build a ``Sentence`` from a read-adapter row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            ordinal=row["ordinal"],
            text=row["text"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            cursor=row.get("cursor") or "",
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
class EntityMention:
    """One named-entity mention (NER layer) with its surface text and span."""

    id: strawberry.ID
    document_id: strawberry.ID
    title: str
    entity_type: str
    text: str
    char_start: int
    char_end: int
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> EntityMention:
        """Build an ``EntityMention`` from a read-adapter row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            document_id=strawberry.ID(str(row["doc_id"])),
            title=row["title"],
            entity_type=row["entity_type"],
            text=row.get("text") or "",
            char_start=row["char_start"],
            char_end=row["char_end"],
            cursor=row.get("cursor") or "",
        )


@strawberry.type
class EntityFrequency:
    """A (surface text, entity type) frequency row — character/place/org tables."""

    text: str
    entity_type: str
    count: int
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> EntityFrequency:
        """Build an ``EntityFrequency`` from a read-adapter row."""
        return cls(
            text=row.get("text") or "",
            entity_type=row["entity_type"],
            count=int(row["count"]),
            cursor=row.get("cursor") or "",
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
    lifecycle_state: str
    archived_at: datetime | None
    deleted_at: datetime | None
    error: str | None
    page_count: int | None
    char_count: int | None
    byte_size: int | None
    content_sha256: str | None
    source_uri: str | None
    original_filename: str | None
    description: str | None
    subtitle: str | None
    publisher: str | None
    published_on: date | None
    license: str | None
    created_at: datetime
    updated_at: datetime | None
    visibility: str
    cursor: str

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
            lifecycle_state=row.get("lifecycle_state") or "active",
            archived_at=row.get("archived_at"),
            deleted_at=row.get("deleted_at"),
            error=row.get("error"),
            page_count=row.get("page_count"),
            char_count=row.get("char_count"),
            byte_size=row.get("byte_size"),
            content_sha256=row.get("content_sha256"),
            source_uri=row.get("source_uri"),
            original_filename=row.get("original_filename") or row.get("filename"),
            description=row.get("description"),
            subtitle=row.get("subtitle"),
            publisher=row.get("publisher"),
            published_on=row.get("published_on"),
            license=row.get("license"),
            created_at=row["created_at"],
            updated_at=row.get("updated_at"),
            visibility=row.get("visibility") or "private",
            cursor=row.get("cursor") or str(row["id"]),
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
    async def collections(self, info: Info) -> list[DocumentCollection]:
        """Return collections that include this document."""
        rows = await read.collections_for_document(info.context.conn, str(self.id))
        return [DocumentCollection.from_row(row) for row in rows]

    @strawberry.field
    async def sentence_count(self, info: Info) -> int:
        """Resolve the document's sentence count via the batched loader."""
        return await info.context.loaders.sentence_count.load(str(self.id))

    @strawberry.field
    async def token_count(self, info: Info) -> int:
        """Resolve the document's token count via the batched loader."""
        return await info.context.loaders.token_count.load(str(self.id))

    @strawberry.field
    async def has_position_data(self, info: Info) -> bool:
        """Return whether the document has persisted page/bbox geometry."""
        from doc_ray.adapters import persistence_read as read

        return await read.document_has_position_data(info.context.conn, str(self.id))

    @strawberry.field
    async def entities(
        self,
        info: Info,
        entity_type: str | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> list[EntityMention]:
        """Resolve a bounded page of this document's named-entity mentions."""
        ctx = info.context
        rows = await read.list_entities(
            ctx.conn,
            document_id=str(self.id),
            entity_type=entity_type,
            limit=ctx.page_size(limit),
            after=after,
        )
        return [EntityMention.from_row(r) for r in rows]

    @strawberry.field
    async def sentences(
        self, info: Info, limit: int = 40, after: str | None = None
    ) -> list[Sentence]:
        """Resolve a bounded page of the document's sentences."""
        ctx = info.context
        rows = await read.list_sentences(
            ctx.conn, str(self.id), limit=ctx.page_size(limit), after=after
        )
        return [Sentence.from_row(r) for r in rows]

    @strawberry.field
    async def provenance(self, info: Info) -> list[ProvenanceEvent]:
        """Resolve the document's provenance trail."""
        rows = await read.get_provenance(info.context.conn, str(self.id))
        return [ProvenanceEvent.from_row(r) for r in rows]

    @strawberry.field
    async def annotation_runs(
        self, info: Info, limit: int = 50, after: str | None = None
    ) -> list[AnnotationRun]:
        """Resolve bounded annotation runs for this document."""
        ctx = info.context
        rows = await layer_read.list_annotation_runs(
            ctx.conn, str(self.id), limit=ctx.page_size(limit), after=after
        )
        return [AnnotationRun.from_row(r) for r in rows]

    @strawberry.field
    async def tokens(self, info: Info, limit: int = 100, after: str | None = None) -> list[Token]:
        """Resolve bounded token rows with morphology for this document."""
        ctx = info.context
        rows = await layer_read.list_tokens(
            ctx.conn, str(self.id), limit=ctx.page_size(limit), after=after
        )
        return [Token.from_row(r) for r in rows]

    @strawberry.field
    async def dependencies(
        self, info: Info, limit: int = 100, after: str | None = None
    ) -> list[DependencyEdge]:
        """Resolve bounded dependency edges for this document."""
        ctx = info.context
        rows = await layer_read.list_dependencies(
            ctx.conn, str(self.id), limit=ctx.page_size(limit), after=after
        )
        return [DependencyEdge.from_row(r) for r in rows]

    @strawberry.field
    async def chunks(self, info: Info, limit: int = 100, after: str | None = None) -> list[Chunk]:
        """Resolve bounded shallow chunks for this document."""
        ctx = info.context
        rows = await layer_read.list_chunks(
            ctx.conn, str(self.id), limit=ctx.page_size(limit), after=after
        )
        return [Chunk.from_row(r) for r in rows]

    @strawberry.field
    async def coref_chains(
        self, info: Info, limit: int = 50, after: str | None = None
    ) -> list[CorefChain]:
        """Resolve bounded coreference chains for this document."""
        ctx = info.context
        rows = await layer_read.list_coref_chains(
            ctx.conn, str(self.id), limit=ctx.page_size(limit), after=after
        )
        return [CorefChain.from_row(r) for r in rows]

    @strawberry.field
    async def entity_links(
        self, info: Info, limit: int = 100, after: str | None = None
    ) -> list[EntityLink]:
        """Resolve bounded entity-link rows for this document."""
        ctx = info.context
        rows = await layer_read.list_entity_links(
            ctx.conn, str(self.id), limit=ctx.page_size(limit), after=after
        )
        return [EntityLink.from_row(r) for r in rows]
