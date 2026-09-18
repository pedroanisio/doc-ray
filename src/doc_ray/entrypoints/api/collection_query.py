"""GraphQL query mixin — document collections and the lenses derived from them.

Split out of :mod:`doc_ray.entrypoints.api.query` (D-015), following the
``StructureQueries``/``VisualQueries`` sibling precedent: ``CollectionQueries``
is mixed into the root ``Query`` so the schema is unchanged. Resolvers stay thin
— clamp the page size, delegate to the async read adapter, map rows to types.

A *lens* is a ``document_collection`` marked ``isScope``, so the lens
read lives beside the collection reads it narrows.
"""

from __future__ import annotations

import strawberry

from doc_ray.adapters import persistence_read as read
from doc_ray.entrypoints.api.ids import clean_uuid as _clean_uuid
from doc_ray.entrypoints.api.types import DocumentCollection, Info


@strawberry.type
class CollectionQueries:
    """Collection and lens reads, inherited by the root ``Query``."""

    @strawberry.field
    async def collections(
        self,
        info: Info,
        limit: int = 50,
        after: str | None = None,
        kind: str | None = None,
    ) -> list[DocumentCollection]:
        """Return manual and smart document collections with visible document counts."""
        rows = await read.list_collections(
            info.context.conn,
            limit=info.context.page_size(limit),
            after=after,
            kind=kind,
        )
        return [DocumentCollection.from_row(row) for row in rows]

    @strawberry.field
    async def collection(self, info: Info, id: strawberry.ID) -> DocumentCollection | None:
        """Return one document collection, or ``None`` if *id* is malformed or unknown."""
        collection_id = _clean_uuid(str(id))
        if collection_id is None:
            return None
        row = await read.get_collection(info.context.conn, collection_id)
        return DocumentCollection.from_row(row) if row is not None else None

    @strawberry.field
    async def lenses(
        self,
        info: Info,
        limit: int = 50,
        after: str | None = None,
    ) -> list[DocumentCollection]:
        """Return the lenses — the collections marked as entity-resolution scopes.

        A lens is a ``DocumentCollection`` with ``isScope`` true; the reserved
        ``global`` lens is always present. This is ``collections`` narrowed to
        scopes and is the read side of ``promoteCollectionToLens``. Pass a row's
        ``cursor`` as ``after`` to page (keyset-ordered by name).
        """
        rows = await read.list_lenses(
            info.context.conn,
            limit=info.context.page_size(limit),
            after=after,
        )
        return [DocumentCollection.from_row(row) for row in rows]
