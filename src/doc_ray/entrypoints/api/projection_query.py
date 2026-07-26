"""GraphQL reads for the projection engine (SLP Block D, issue #8).

A query group split out of ``query.py`` as its own mixin (the ``StructureQueries`` /
``ClassificationQueries`` pattern, D-015), mixed into the ``Query`` root. Both fields
are bounded, keyset-paginated, and read only derived tables (D-024): no live
aggregate over a fact table.
"""

from __future__ import annotations

import strawberry

from doc_ray.adapters import persistence_projection_read as projection_read
from doc_ray.entrypoints.api.ids import clean_uuid as _clean_uuid
from doc_ray.entrypoints.api.types import EntityProjection, Info, LensProjection


@strawberry.type
class ProjectionQueries:
    """Lens-scoped entity identity reads (SLP Block D)."""

    @strawberry.field
    async def entity_projection(
        self,
        info: Info,
        document_id: strawberry.ID,
        lens_id: strawberry.ID | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> LensProjection:
        """Return a document's entity projection through a lens (Block D, #8).

        With no ``lensId``, the document's default lens is used, else the ``global``
        lens — so an existing caller keeps its current, global behavior. When the
        document belongs to several projecting lenses with no default, the result
        falls back to ``global`` and sets ``ambiguous`` (it never picks a scope lens
        arbitrarily). Each row's ``status`` is ``proposed`` until a human confirms it
        (PALS's Law). Page with a row's ``cursor`` as ``after`` (ordered by mention).
        """
        ctx = info.context
        doc_id = _clean_uuid(str(document_id))
        if doc_id is None:
            return LensProjection(
                lens_id=None, is_default=False, ambiguous=False, entities=[], next_cursor=None
            )
        raw_lens = _clean_uuid(str(lens_id)) if lens_id is not None else None
        effective, is_default, ambiguous = await projection_read.resolve_effective_lens(
            ctx.conn, doc_id, raw_lens
        )
        if effective is None:
            return LensProjection(
                lens_id=None, is_default=False, ambiguous=ambiguous, entities=[], next_cursor=None
            )
        rows = await projection_read.entity_projection(
            ctx.conn, doc_id, effective, limit=ctx.page_size(limit), after=after
        )
        entities = [EntityProjection.from_row(row) for row in rows]
        return LensProjection(
            lens_id=strawberry.ID(effective),
            is_default=is_default,
            ambiguous=ambiguous,
            entities=entities,
            next_cursor=entities[-1].cursor if entities else None,
        )
