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
from doc_ray.entrypoints.api.correction_types import (
    EntityMentionCorrection as EntityMentionCorrection,
)
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
from doc_ray.entrypoints.api.sentence_search_types import SentenceHit as SentenceHit


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
class CorpusShardInfo:
    """One shard's document count in a corpus-partition manifest."""

    index: int
    document_count: int


@strawberry.type
class CorpusManifest:
    """The shard map for partitioning the visible corpus into ``shardCount`` parts.

    Deterministic (``hashtext(id) mod shardCount``) and disjoint, so an
    orchestrator can fan out one worker per shard and prove coverage: the
    per-shard counts sum to ``totalDocuments``.
    """

    shard_count: int
    total_documents: int
    shards: list[CorpusShardInfo]

    @classmethod
    def from_rows(cls, shard_count: int, rows: list[Row]) -> CorpusManifest:
        """Build a manifest from ``{shard, n}`` count rows (one per shard 0..N-1)."""
        shards = [CorpusShardInfo(index=int(r["shard"]), document_count=int(r["n"])) for r in rows]
        return cls(
            shard_count=shard_count,
            total_documents=sum(s.document_count for s in shards),
            shards=shards,
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


@strawberry.type
class DocumentClassification:
    """A document's genre classification (SLP Block C, #6).

    The genre axis (fiction / non-fiction / mixed / unknown) that the
    per-entity-type resolution policy reads. ``method`` is ``'heuristic'`` for a
    machine guess or ``'human'`` for a confirmation; ``status`` is ``'pending'``
    (an unverified machine guess — do NOT rely on it for identity decisions),
    ``'confirmed'`` (a human decided), or ``'rejected'``. A ``pending`` genre is a
    GUESS: this block defines the policy, it does not apply it (Block D, #8, does).
    """

    document_id: strawberry.ID
    genre: str
    subtype: str | None
    confidence: float | None
    method: str
    status: str
    reviewer: strawberry.ID | None
    rationale: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Row) -> DocumentClassification:
        """Build a ``DocumentClassification`` from a ``document_classification`` row."""
        reviewer = row.get("reviewer")
        return cls(
            document_id=strawberry.ID(str(row["document_id"])),
            genre=row["genre"],
            subtype=row.get("subtype"),
            confidence=row.get("confidence"),
            method=row["method"],
            status=row["status"],
            reviewer=strawberry.ID(str(reviewer)) if reviewer else None,
            rationale=row.get("rationale"),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
        )


@strawberry.type
class DocumentShare:
    """One ``document_acl`` grant: a principal, the permission, and when it was made."""

    principal_id: strawberry.ID
    email: str | None
    permission: str
    granted_at: datetime

    @classmethod
    def from_row(cls, row: Row) -> DocumentShare:
        """Build a ``DocumentShare`` from a joined ``document_acl`` row."""
        return cls(
            principal_id=strawberry.ID(str(row["principal_id"])),
            email=row.get("email"),
            permission=row["permission"],
            granted_at=cast(datetime, row["granted_at"]),
        )


@strawberry.type
class EntityProjection:
    """One mention's derived identity within a lens (SLP Block D, #8).

    ``project(mention, lens)`` materialized: the mention is the immutable fact, and
    this is *who it is* through one lens. ``scope`` is ``'local'`` (a work-local
    identity) or ``'global'`` (the shared real-world registry); ``canonicalId`` is
    null when the mention is **explicitly unresolved** (matched no identity, not
    dropped). ``status`` is ``'proposed'`` (a machine guess — do NOT treat as a
    settled fact, PALS's Law), ``'confirmed'``, or ``'rejected'``. ``method`` is how
    it resolved (``'exact_form'`` / ``'unresolved'`` / ``'human'``). The span
    (``charStart``/``charEnd``) and ``surface`` cite the mention back to the text.
    """

    mention_id: strawberry.ID
    lens_id: strawberry.ID
    document_id: strawberry.ID
    canonical_id: int | None
    scope: str
    method: str
    confidence: float | None
    status: str
    surface: str
    mention_type: str
    char_start: int
    char_end: int
    preferred: str | None
    canonical_type: str | None
    wikidata_id: str | None
    refreshed_at: datetime
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> EntityProjection:
        """Build an ``EntityProjection`` from a projection read row."""
        cid = row.get("canonical_id")
        return cls(
            mention_id=strawberry.ID(str(row["mention_id"])),
            lens_id=strawberry.ID(str(row["lens_id"])),
            document_id=strawberry.ID(str(row["document_id"])),
            canonical_id=int(cid) if cid is not None else None,
            scope=row["scope"],
            method=row["method"],
            confidence=row.get("confidence"),
            status=row["status"],
            surface=row["surface"],
            mention_type=row["mention_type"],
            char_start=int(row["char_start"]),
            char_end=int(row["char_end"]),
            preferred=row.get("preferred"),
            canonical_type=row.get("canonical_type"),
            wikidata_id=row.get("wikidata_id"),
            refreshed_at=cast(datetime, row["refreshed_at"]),
            cursor=str(row["cursor"]),
        )


@strawberry.type
class LensProjection:
    """A document's projection through one lens, plus how that lens was chosen.

    ``isDefault`` is true when the lens came from the document's ``defaultLensId``;
    ``ambiguous`` is true when no ``lensId`` was given, no usable default existed, and
    the document belongs to more than one projecting scope lens — so the read fell
    back to ``global`` rather than pick a scope arbitrarily (D-4). ``lensId`` is null
    only if no ``global`` lens exists (never in a migrated database).
    """

    lens_id: strawberry.ID | None
    is_default: bool
    ambiguous: bool
    entities: list[EntityProjection]
    next_cursor: str | None


@strawberry.type
class AllusionEdge:
    """A typed, directional cross-lens link — an allusion, NEVER an identity (SLP Block E, #9).

    Records that one entity *alludes to / depicts / references / is adapted from /
    inspired by* another, across lenses (e.g. a novel's Paris → the real Paris), without
    merging them. **An edge is not an equality**: nothing about projection, resolution, or
    the canonical registry changes because it exists, and edges are **never transitive**
    (A→B and B→C does not imply A→C or A≡C). ``status`` is ``proposed`` (an unverified
    machine/agent guess — do not act on it as fact, PALS's Law), ``confirmed``, or
    ``rejected``. The ``from``/``to`` endpoints each name a ``(canonicalId, lensId)``.
    """

    id: strawberry.ID
    from_canonical_id: int
    from_lens_id: strawberry.ID
    to_canonical_id: int
    to_lens_id: strawberry.ID
    edge_type: str
    confidence: float | None
    method: str
    status: str
    reviewer: strawberry.ID | None
    rationale: str | None
    evidence_document_id: strawberry.ID | None
    created_at: datetime
    from_preferred: str | None
    from_wikidata_id: str | None
    to_preferred: str | None
    to_wikidata_id: str | None
    from_lens_name: str | None
    to_lens_name: str | None
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> AllusionEdge:
        """Build an ``AllusionEdge`` from a joined read row or a hydrated write row."""
        reviewer = row.get("reviewer")
        evidence = row.get("evidence_document_id")
        return cls(
            id=strawberry.ID(str(row["id"])),
            from_canonical_id=int(row["from_canonical_id"]),
            from_lens_id=strawberry.ID(str(row["from_lens_id"])),
            to_canonical_id=int(row["to_canonical_id"]),
            to_lens_id=strawberry.ID(str(row["to_lens_id"])),
            edge_type=row["edge_type"],
            confidence=row.get("confidence"),
            method=row["method"],
            status=row["status"],
            reviewer=strawberry.ID(str(reviewer)) if reviewer else None,
            rationale=row.get("rationale"),
            evidence_document_id=strawberry.ID(str(evidence)) if evidence else None,
            created_at=cast(datetime, row["created_at"]),
            from_preferred=row.get("from_preferred"),
            from_wikidata_id=row.get("from_wikidata_id"),
            to_preferred=row.get("to_preferred"),
            to_wikidata_id=row.get("to_wikidata_id"),
            from_lens_name=row.get("from_lens_name"),
            to_lens_name=row.get("to_lens_name"),
            cursor=str(row.get("cursor") or row["id"]),
        )
