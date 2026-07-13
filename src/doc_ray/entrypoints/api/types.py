"""Strawberry GraphQL types over the document store: lexemes, search, jobs.

The document cluster (``Document``, ``DocumentCollection``, ``Sentence``,
``ProvenanceEvent``, ``EntityMention``, ``EntityFrequency``, ``MetadataEntry``)
lives in :mod:`.document_types` (D-015) and is re-exported here, so this module
remains the stable import surface. Class and field docstrings double as the
GraphQL schema descriptions.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

import strawberry
from strawberry.scalars import JSON

from doc_ray.adapters import persistence_read as read
from doc_ray.entrypoints.api.document_types import (
    Document as Document,
)
from doc_ray.entrypoints.api.document_types import (
    DocumentCollection as DocumentCollection,
)
from doc_ray.entrypoints.api.document_types import (
    EntityFrequency as EntityFrequency,
)
from doc_ray.entrypoints.api.document_types import (
    EntityMention as EntityMention,
)
from doc_ray.entrypoints.api.document_types import (
    Info as Info,
)
from doc_ray.entrypoints.api.document_types import (
    MetadataEntry as MetadataEntry,
)
from doc_ray.entrypoints.api.document_types import (
    ProvenanceEvent as ProvenanceEvent,
)
from doc_ray.entrypoints.api.document_types import (
    Row as Row,
)
from doc_ray.entrypoints.api.document_types import (
    Sentence as Sentence,
)


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
    cursor: str

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
            cursor=row.get("cursor") or "",
        )


@strawberry.type
class Lexeme:
    """A lexeme; its occurrences resolve lazily as keyword-in-context."""

    uid: strawberry.ID
    lang: str
    pos: str
    lemma: str
    uses: int | None
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> Lexeme:
        """Build a ``Lexeme`` from a read-adapter row."""
        return cls(
            uid=strawberry.ID(str(row["uid"])),
            lang=row["lang"],
            pos=row["pos"],
            lemma=row["lemma"],
            uses=row.get("uses"),
            cursor=row.get("cursor") or str(row["uid"]),
        )

    @strawberry.field
    async def occurrences(
        self,
        info: Info,
        document_id: strawberry.ID | None = None,
        limit: int = 80,
        after: str | None = None,
    ) -> list[LexemeOccurrence]:
        """Resolve a bounded page of this lexeme's occurrences.

        Pass ``documentId`` to scope to one document, and ``after`` to request
        the next keyset page.
        """
        ctx = info.context
        rows = await read.lexeme_kwic(
            ctx.conn,
            str(self.uid),
            document_id=str(document_id) if document_id is not None else None,
            limit=ctx.page_size(limit),
            after=after,
        )
        return [LexemeOccurrence.from_row(r) for r in rows]


@strawberry.type
class SentenceHit:
    """A sentence search hit with a highlightable pre/hit/post split.

    ``id`` is the sentence's stable ULID, so a hit can be cited or re-fetched
    without an ordinal round-trip through ``document.sentences``.
    """

    id: strawberry.ID
    document_id: strawberry.ID
    title: str
    ordinal: int
    text: str
    pre: str
    hit: str
    post: str
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> SentenceHit:
        """Build a ``SentenceHit`` from a read-adapter row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            document_id=strawberry.ID(str(row["doc_id"])),
            title=row["title"],
            ordinal=row["ordinal"],
            text=row["text"],
            pre=row.get("pre") or "",
            hit=row.get("hit") or "",
            post=row.get("post") or "",
            cursor=row.get("cursor") or "",
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


@strawberry.type
class Job:
    """A background job's status (returned by mutations and the job query)."""

    id: strawberry.ID
    kind: str
    status: str
    document_id: strawberry.ID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    attempts: int
    max_attempts: int
    error: str | None
    result: JSON | None
    progress_current: int | None
    progress_total: int | None
    progress_stage: str | None
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> Job:
        """Build a ``Job`` from a queue status row."""
        doc = row.get("document_id")
        res = row.get("result")
        return cls(
            id=strawberry.ID(str(row["id"])),
            kind=row["kind"],
            status=row["status"],
            document_id=strawberry.ID(str(doc)) if doc is not None else None,
            created_at=row["created_at"],
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            error=row.get("error"),
            result=cast(JSON, res) if res is not None else None,
            progress_current=row.get("progress_current"),
            progress_total=row.get("progress_total"),
            progress_stage=row.get("progress_stage"),
            cursor=row.get("cursor") or str(row["id"]),
        )


@strawberry.type
class SeedSuggestion:
    """A mined, KB-resolved seed candidate awaiting curation.

    The audit surface for domain seeding: ``description`` is what lets a curator
    catch a wrong-identity resolution before it reaches the registry.
    """

    id: strawberry.ID
    document_id: strawberry.ID
    term: str
    occurrences: int
    source: str
    qid: str | None
    label: str | None
    description: str | None
    entity_type: str | None
    status: str

    @classmethod
    def from_row(cls, row: Row) -> SeedSuggestion:
        """Build a ``SeedSuggestion`` from a store row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            document_id=strawberry.ID(str(row["document_id"])),
            term=row["term"],
            occurrences=row["occurrences"],
            source=row["source"],
            qid=row.get("qid"),
            label=row.get("label"),
            description=row.get("description"),
            entity_type=row.get("entity_type"),
            status=row["status"],
        )
