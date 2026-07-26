"""GraphQL reads for hyper-context lens rollups (SLP Block F, issue #10, FR-5/FR-6).

A query group split out as its own mixin (D-015), mixed into the ``Query`` root. Three
reads, all bounded, keyset-paginated, and served from precomputed rollups (never a live
aggregate over a fact table — D-024). ``lensEntities`` is the composable lens parameter
on the entity landscape (FR-6: read the same corpus through different lenses); relations
are a co-occurrence measure, not a claim of real relationship (PALS's Law).
"""

from __future__ import annotations

import strawberry

from doc_ray.adapters import persistence_lens_rollup_read as rollup_read
from doc_ray.entrypoints.api.ids import clean_uuid as _clean_uuid
from doc_ray.entrypoints.api.lens_rollup_types import EntityRelation, LensEntity, RelatedEntity
from doc_ray.entrypoints.api.types import Info


@strawberry.type
class LensRollupQueries:
    """Hyper-context reads: a lens's entity landscape and its relationships (SLP Block F)."""

    @strawberry.field
    async def lens_entities(
        self,
        info: Info,
        lens_id: strawberry.ID,
        limit: int = 100,
        after: str | None = None,
        min_mentions: int = 0,
    ) -> list[LensEntity]:
        """Return a lens's significant entities, most-mentioned first (Block F, #10).

        Reads the ``lens_entity_rollup`` — O(result), never a live aggregate (D-024). Each
        entry carries its type profile + consistency, first-seen ordinal, and a
        ``refreshedAt`` staleness stamp. ``minMentions`` filters low-volume entities.
        Scope lenses only (the corpus-wide global distribution is served by
        ``entityProfiles`` / ``entityFrequency``). Page with a row's ``cursor``.
        """
        ctx = info.context
        raw_lens = _clean_uuid(str(lens_id))
        if raw_lens is None:
            return []
        rows = await rollup_read.lens_entities(
            ctx.conn, raw_lens, limit=ctx.page_size(limit), after=after, min_mentions=min_mentions
        )
        return [LensEntity.from_row(row) for row in rows]

    @strawberry.field
    async def entity_relations(
        self,
        info: Info,
        lens_id: strawberry.ID,
        canonical_id: int,
        limit: int = 50,
        after: str | None = None,
        min_strength: float = 0.0,
    ) -> list[EntityRelation]:
        """Return an entity's direct co-occurrence relations in a lens, strongest first (#10).

        ``strength`` is an NPMI co-occurrence measure in ``[-1, 1]`` — a **structural**
        signal, NOT a claim that the entities are truly related (PALS's Law).
        ``minStrength`` filters weak associations. Each row is the *other* endpoint of the
        relationship. Reads the rollup (D-024). Page with a row's ``cursor``.
        """
        ctx = info.context
        raw_lens = _clean_uuid(str(lens_id))
        if raw_lens is None:
            return []
        rows = await rollup_read.entity_relations(
            ctx.conn,
            raw_lens,
            canonical_id,
            limit=ctx.page_size(limit),
            after=after,
            min_strength=min_strength,
        )
        return [EntityRelation.from_row(row) for row in rows]

    @strawberry.field
    async def related_entities(
        self, info: Info, lens_id: strawberry.ID, canonical_id: int, hops: int = 2, limit: int = 50
    ) -> list[RelatedEntity]:
        """Discover entities within *hops* co-occurrence steps of an anchor (bounded N-hop, #10).

        Breadth-first over the lens's co-occurrence graph, returning each entity's shortest
        ``hops`` distance. ``hops`` is capped at 3 and a larger request is **rejected**, not
        silently clamped — an unbounded graph walk is out of scope by design. Reachability
        is a structural signal, not an assertion of a real relationship (PALS's Law).
        """
        ctx = info.context
        raw_lens = _clean_uuid(str(lens_id))
        if raw_lens is None:
            return []
        rows = await rollup_read.related_entities(
            ctx.conn, raw_lens, canonical_id, hops=hops, limit=ctx.page_size(limit)
        )
        return [RelatedEntity.from_row(row) for row in rows]
