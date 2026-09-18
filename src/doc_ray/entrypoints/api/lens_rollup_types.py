"""Strawberry GraphQL types for the hyper-context lens rollups (issue #10).

Split out of :mod:`.types` (D-015): the per-lens entity landscape (``LensEntity``) and
its relationships (``EntityRelation``, ``RelatedEntity``). Class and field docstrings are
the published schema descriptions. Every field is served from a precomputed rollup with a
``refreshedAt`` staleness stamp; relationship strength is a co-occurrence measure, never a
claim of a real relationship (PALS's Law).
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

import strawberry
from strawberry.scalars import JSON

from doc_ray.entrypoints.api.document_types import Row


@strawberry.type
class LensEntity:
    """A significant entity in a lens, from the hyper-context rollup (issue #10).

    The read side of ``lens_entity_rollup``: ``nMentions`` / ``nDocuments`` are lens-wide
    counts (not filtered to the caller's ACL subset — the same trade-off ``corpusStats``
    makes). ``typeProfile`` is the observed ``{type: count}`` spread and
    ``typeConsistency`` its dominant share (near 1.0 = coherent; low = the annotator
    disagreed with itself — PALS's Law at the read surface). ``scope`` is work-local vs
    shared real-world. ``firstSeenOrdinal`` cites the earliest sentence. ``refreshedAt``
    is the staleness stamp — this is a precomputed rollup, not a live count.
    """

    canonical_id: int
    preferred: str
    entity_type: str
    wikidata_id: str | None
    scope: str
    n_mentions: int
    n_documents: int
    type_profile: JSON
    type_consistency: float | None
    first_seen_ordinal: int | None
    refreshed_at: datetime
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> LensEntity:
        """Build a ``LensEntity`` from a ``lens_entity_rollup`` join row."""
        return cls(
            canonical_id=int(row["canonical_id"]),
            preferred=row["preferred"],
            entity_type=row["entity_type"],
            wikidata_id=row.get("wikidata_id"),
            scope=row["scope"],
            n_mentions=int(row["mention_count"]),
            n_documents=int(row["document_count"]),
            type_profile=cast(JSON, row.get("type_profile") or {}),
            type_consistency=row.get("type_consistency"),
            first_seen_ordinal=row.get("first_seen_ordinal"),
            refreshed_at=cast(datetime, row["refreshed_at"]),
            cursor=str(row["cursor"]),
        )


@strawberry.type
class EntityRelation:
    """A direct co-occurrence relationship between two entities in a lens.

    ``cooccurrenceCount`` is how many sentences the two entities share; ``strength`` is
    the NPMI (Bouma 2009) of that co-occurrence — a **structural association measure in
    [-1, 1], NOT a claim of a real-world relationship** (PALS's Law). The row describes
    the *other* endpoint (``canonicalId`` etc.) relative to the queried entity.
    ``refreshedAt`` is the rollup's staleness stamp.
    """

    canonical_id: int
    preferred: str
    entity_type: str
    wikidata_id: str | None
    cooccurrence_count: int
    strength: float
    refreshed_at: datetime
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> EntityRelation:
        """Build an ``EntityRelation`` from an ``entity_relations`` read row."""
        return cls(
            canonical_id=int(row["other_canonical_id"]),
            preferred=row["preferred"],
            entity_type=row["entity_type"],
            wikidata_id=row.get("wikidata_id"),
            cooccurrence_count=int(row["cooccurrence_count"]),
            strength=float(row["strength"]),
            refreshed_at=cast(datetime, row["refreshed_at"]),
            cursor=str(row["cursor"]),
        )


@strawberry.type
class RelatedEntity:
    """An entity reachable from an anchor within N co-occurrence hops.

    ``hops`` is the shortest number of co-occurrence steps from the anchor (bounded by an
    enforced maximum). Reachability via co-occurrence is a **structural** signal, never
    an assertion that the entities are truly related (PALS's Law).
    """

    canonical_id: int
    preferred: str
    entity_type: str
    wikidata_id: str | None
    hops: int

    @classmethod
    def from_row(cls, row: Row) -> RelatedEntity:
        """Build a ``RelatedEntity`` from a ``related_entities`` read row."""
        return cls(
            canonical_id=int(row["canonical_id"]),
            preferred=row["preferred"],
            entity_type=row["entity_type"],
            wikidata_id=row.get("wikidata_id"),
            hops=int(row["hops"]),
        )
