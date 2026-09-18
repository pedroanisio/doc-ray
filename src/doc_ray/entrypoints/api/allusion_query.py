"""GraphQL read for allusion edges (issue #9).

A query group split out of ``query.py`` as its own mixin (the ``StructureQueries`` /
``ProjectionQueries`` pattern, D-015), mixed into the ``Query`` root. Bounded and
keyset-paginated; reads only the RLS-scoped ``entity_allusion`` join (D-024). RLS hides
any edge whose far lens the caller cannot see, so this never reveals an unreadable
side's entity.
"""

from __future__ import annotations

import strawberry

from doc_ray.adapters import persistence_allusion_read as allusion_read
from doc_ray.domain.allusions import normalize_direction
from doc_ray.entrypoints.api.types import AllusionEdge, Info


@strawberry.type
class AllusionQueries:
    """Cross-lens allusion reads."""

    @strawberry.field
    async def allusions(
        self,
        info: Info,
        canonical_id: int,
        lens_id: strawberry.ID,
        direction: str = "both",
        limit: int = 50,
        after: str | None = None,
    ) -> list[AllusionEdge]:
        """Return allusion edges anchored on an entity in a lens (issue #9).

        ``direction`` = ``outgoing`` (what this entity alludes to), ``incoming`` (what
        alludes to it), or ``both`` (default). An allusion is a typed link, **never an
        identity**: do not treat a returned edge as an equality, and do not chain edges
        transitively (A→B, B→C does not imply A→C). ``status`` tells you whether an edge
        is a ``proposed`` guess or a ``confirmed`` human decision. Only edges whose both
        lenses (and evidence document) you may see are returned. Page with a row's
        ``cursor`` as ``after`` (newest first).
        """
        parsed = normalize_direction(direction)  # raises on an unknown direction
        rows = await allusion_read.list_allusions(
            info.context.conn,
            canonical_id=canonical_id,
            lens_id=str(lens_id),
            direction=parsed,
            limit=info.context.page_size(limit),
            after=after,
        )
        return [AllusionEdge.from_row(row) for row in rows]
