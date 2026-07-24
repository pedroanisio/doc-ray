"""Document-collection mutations (BL-057), merged into the ``Mutation`` root.

Split out of :mod:`doc_ray.entrypoints.api.mutation` (D-015):
``CollectionMutations`` is a Strawberry type the root ``Mutation`` class
inherits, so the schema is unchanged.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import strawberry
from strawberry.scalars import JSON

from doc_ray.adapters import persistence_read as read
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.mutation_guards import (
    _audit_mutation_event,
    _require_document_write,
    _require_write_audited,
)
from doc_ray.entrypoints.api.types import DocumentCollection, Info


def _clean_collection_kind(kind: str) -> str:
    """Validate and normalize a collection kind argument."""
    clean = kind.strip().lower()
    if clean not in {"manual", "smart"}:
        raise ValueError("collection kind must be 'manual' or 'smart'")
    return clean


def _clean_collection_rule(
    rule: JSON | None,
    *,
    doc_type: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Normalize a smart-collection rule to its allow-listed keys."""
    clean: dict[str, Any] = dict(rule) if isinstance(rule, dict) else {}
    if doc_type is not None and doc_type.strip():
        clean["doc_type"] = doc_type.strip().lower()
    if status is not None and status.strip():
        clean["status"] = status.strip().lower()
    if tags is not None:
        clean_tags = sorted({tag.strip() for tag in tags if tag and tag.strip()})
        if clean_tags:
            clean["tags"] = clean_tags
        else:
            clean.pop("tags", None)
    return {
        key: value
        for key, value in clean.items()
        if key in {"doc_type", "status", "tags"} and value not in (None, "", [])
    }


async def _collection_status(ctx: RequestContext, collection_id: str) -> DocumentCollection:
    """Load one collection row and map it to its GraphQL type."""
    try:
        uuid.UUID(collection_id)
    except ValueError as exc:
        raise ValueError(f"unknown collection {collection_id}") from exc
    row = await read.get_collection(ctx.conn, collection_id)
    if row is None:
        raise ValueError(f"unknown collection {collection_id}")
    return DocumentCollection.from_row(row)


async def _require_manual_collection(ctx: RequestContext, collection_id: str) -> None:
    """Reject membership edits against unknown or smart collections."""
    try:
        uuid.UUID(collection_id)
    except ValueError as exc:
        raise ValueError(f"unknown collection {collection_id}") from exc
    row = await read.get_collection(ctx.conn, collection_id)
    if row is None:
        raise ValueError(f"unknown collection {collection_id}")
    if row["kind"] != "manual":
        raise ValueError("only manual collections can be edited directly")


@strawberry.type
class CollectionMutations:
    """Collection create/membership mutations, inherited by ``Mutation``."""

    @strawberry.mutation
    async def create_collection(
        self,
        info: Info,
        name: str,
        description: str = "",
        kind: str = "manual",
        rule: JSON | None = None,
        doc_type: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
    ) -> DocumentCollection:
        """Create a manual or smart document collection and return it."""
        ctx = info.context
        await _require_write_audited(ctx, action="mutation.create_collection")
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("collection name is required")
        clean_kind = _clean_collection_kind(kind)
        clean_rule = (
            _clean_collection_rule(rule, doc_type=doc_type, status=status, tags=tags)
            if clean_kind == "smart"
            else {}
        )
        cur = await ctx.conn.execute(
            """
            INSERT INTO document_collections (name, description, kind, rule)
            VALUES (%s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (clean_name, description.strip(), clean_kind, json.dumps(clean_rule, sort_keys=True)),
        )
        row = await cur.fetchone()
        if row is None:  # INSERT ... RETURNING always yields a row; guard the contract
            raise RuntimeError("collection insert returned no row")
        await _audit_mutation_event(
            ctx,
            action="mutation.create_collection",
            target_type="collection",
            target_id=str(row["id"]),
            outcome="success",
        )
        return await _collection_status(ctx, str(row["id"]))

    @strawberry.mutation
    async def add_document_to_collection(
        self, info: Info, collection_id: strawberry.ID, document_id: strawberry.ID
    ) -> DocumentCollection:
        """Add one document to a manual collection."""
        ctx = info.context
        doc_id = str(document_id)
        coll_id = str(collection_id)
        await _require_document_write(ctx, doc_id, action="mutation.add_document_to_collection")
        await _require_manual_collection(ctx, coll_id)
        await ctx.conn.execute(
            """
            INSERT INTO document_collection_members (collection_id, document_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (coll_id, doc_id),
        )
        await _audit_mutation_event(
            ctx,
            action="mutation.add_document_to_collection",
            target_type="collection",
            target_id=coll_id,
            outcome="success",
            details={"document_id": doc_id},
        )
        return await _collection_status(ctx, coll_id)

    @strawberry.mutation
    async def remove_document_from_collection(
        self, info: Info, collection_id: strawberry.ID, document_id: strawberry.ID
    ) -> DocumentCollection:
        """Remove one document from a manual collection."""
        ctx = info.context
        doc_id = str(document_id)
        coll_id = str(collection_id)
        await _require_document_write(
            ctx, doc_id, action="mutation.remove_document_from_collection"
        )
        await _require_manual_collection(ctx, coll_id)
        await ctx.conn.execute(
            """
            DELETE FROM document_collection_members
            WHERE collection_id = %s AND document_id = %s
            """,
            (coll_id, doc_id),
        )
        await _audit_mutation_event(
            ctx,
            action="mutation.remove_document_from_collection",
            target_type="collection",
            target_id=coll_id,
            outcome="success",
            details={"document_id": doc_id},
        )
        return await _collection_status(ctx, coll_id)

    @strawberry.mutation
    async def update_collection(
        self,
        info: Info,
        collection_id: strawberry.ID,
        name: str | None = None,
        description: str | None = None,
    ) -> DocumentCollection:
        """Rename a collection and/or edit its description; returns the updated collection.

        Only the fields passed change. Applies to manual and smart collections
        alike — it edits the collection's own metadata, not its membership.
        Renaming to a name another collection already holds is a clean error
        (the ``name`` column is globally unique).
        """
        ctx = info.context
        coll_id = str(collection_id)
        await _require_write_audited(ctx, action="mutation.update_collection")
        await _collection_status(ctx, coll_id)  # rejects an unknown id before any write
        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                raise ValueError("collection name cannot be empty")
            cur = await ctx.conn.execute(
                "SELECT 1 FROM document_collections WHERE name = %s AND id <> %s",
                (clean_name, coll_id),
            )
            if await cur.fetchone() is not None:
                raise ValueError(f"a collection named {clean_name!r} already exists")
            await ctx.conn.execute(
                "UPDATE document_collections SET name = %s WHERE id = %s", (clean_name, coll_id)
            )
        if description is not None:
            await ctx.conn.execute(
                "UPDATE document_collections SET description = %s WHERE id = %s",
                (description.strip(), coll_id),
            )
        await _audit_mutation_event(
            ctx,
            action="mutation.update_collection",
            target_type="collection",
            target_id=coll_id,
            outcome="success",
        )
        return await _collection_status(ctx, coll_id)

    @strawberry.mutation
    async def delete_collection(self, info: Info, collection_id: strawberry.ID) -> bool:
        """Delete a collection; returns True. Membership rows cascade.

        Removes only the container — the member documents themselves are
        untouched (``document_collection_members`` is ``ON DELETE CASCADE``).
        Rejects an unknown collection id.
        """
        ctx = info.context
        coll_id = str(collection_id)
        await _require_write_audited(ctx, action="mutation.delete_collection")
        await _collection_status(ctx, coll_id)  # rejects an unknown id before the delete
        await ctx.conn.execute("DELETE FROM document_collections WHERE id = %s", (coll_id,))
        await _audit_mutation_event(
            ctx,
            action="mutation.delete_collection",
            target_type="collection",
            target_id=coll_id,
            outcome="success",
        )
        return True
