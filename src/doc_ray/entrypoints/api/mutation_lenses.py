"""Lens-registry mutations (issue #5), mixed into the ``Mutation`` root.

Split out of :mod:`doc_ray.entrypoints.api.mutation` (D-015); ``LensMutations``
is a Strawberry type the root ``Mutation`` inherits. A *lens* is a document
collection promoted to a named entity-resolution scope (see the SLP proposal and
``doc_ray.domain.lenses`` for the pure naming/cycle rules).

These three are synchronous metadata edits — no job — on tables docray_client
may write: ``document_collections`` fully (migration 047) and the single
``documents.default_lens_id`` column (migration 121 grant). Promotion is a
curation decision, not a mechanical action; every write emits an audit event on
success AND on denial. The database re-checks every rule the app pre-validates
here (migration 121 triggers), so a clean error is UX, never the only guard.
"""

from __future__ import annotations

import uuid
from typing import Any

import strawberry

from doc_ray.adapters import persistence_read as read
from doc_ray.domain import lenses
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.mutation_guards import (
    _audit_mutation_event,
    _require_document_write,
    _require_write_audited,
)
from doc_ray.entrypoints.api.types import Document, DocumentCollection, Info


def _clean_id(value: str, label: str) -> str:
    """Validate a UUID argument, raising the same opaque error for any bad id."""
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise ValueError(f"unknown {label} {value}") from exc
    return value


async def _load_collection(ctx: RequestContext, collection_id: str) -> dict[str, Any]:
    """Load one collection row or raise; rejects an unknown id before any write."""
    row = await read.get_collection(ctx.conn, collection_id)
    if row is None:
        raise ValueError(f"unknown collection {collection_id}")
    return row


async def _count(ctx: RequestContext, sql: str, params: tuple[Any, ...]) -> int:
    """Run a ``SELECT ... AS n`` count query and return the integer (0 if no row)."""
    cur = await ctx.conn.execute(sql, params)
    row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def _scope_parent_edges(ctx: RequestContext) -> dict[str, str | None]:
    """Return every lens's ``(id -> parent_lens_id)`` edge for cycle pre-checking.

    ``document_collections`` has no RLS (shared navigation metadata), so this is
    the complete lens graph — the input :func:`lenses.would_create_cycle` needs.
    Bounded: only *designated* scope rows, a small set even at 10k documents.
    """
    cur = await ctx.conn.execute(
        "SELECT id, parent_lens_id FROM document_collections WHERE is_scope"
    )
    rows = await cur.fetchall()
    return {str(r["id"]): (str(r["parent_lens_id"]) if r["parent_lens_id"] else None) for r in rows}


async def _deny(
    ctx: RequestContext,
    action: str,
    target_id: str,
    reason: str,
    related: str | None = None,
    *,
    kind: str = "collection",
) -> None:
    """Record a denied lens mutation with a machine-readable reason."""
    details: dict[str, Any] = {"reason": reason}
    if related is not None:
        details["related_id"] = related
    await _audit_mutation_event(
        ctx, action=action, target_type=kind, target_id=target_id, outcome="denied", details=details
    )


@strawberry.type
class LensMutations:
    """Promote/demote lenses and set a document's default lens."""

    @strawberry.mutation
    async def promote_collection_to_lens(
        self,
        info: Info,
        collection_id: strawberry.ID,
        parent_lens_id: strawberry.ID | None = None,
    ) -> DocumentCollection:
        """Promote a collection to a lens — a named entity-resolution scope.

        Marks the collection ``isScope`` so a later projection layer can
        resolve entity identity within it; nothing about resolution changes today.
        Optionally nests it under ``parentLensId``, which must already be a lens —
        nesting is recorded but resolution stays flat here. Promotion is a
        deliberate curation decision: an ordinary collection stays identity-neutral.

        Raises:
            ValueError: unknown collection or parent, a non-scope parent, or a
                parent cycle — rejected before the write (the database re-checks).
        """
        ctx = info.context
        action = "mutation.promote_collection_to_lens"
        await _require_write_audited(ctx, action=action)
        coll_id = _clean_id(str(collection_id), "collection")
        await _load_collection(ctx, coll_id)  # reject unknown before any write
        parent_id: str | None = None
        if parent_lens_id is not None:
            parent_id = _clean_id(str(parent_lens_id), "lens")
            parent = await _load_collection(ctx, parent_id)
            if not parent.get("is_scope"):
                await _deny(ctx, action, coll_id, "parent_not_a_lens", parent_id)
                raise ValueError(f"parent {parent_id} is not a lens (promote it first)")
            edges = await _scope_parent_edges(ctx)
            if lenses.would_create_cycle(edges, coll_id, parent_id):
                await _deny(ctx, action, coll_id, "parent_cycle", parent_id)
                raise ValueError("parentLensId would create a lens cycle")
        await ctx.conn.execute(
            "UPDATE document_collections SET is_scope = true, parent_lens_id = %s WHERE id = %s",
            (parent_id, coll_id),
        )
        await _audit_mutation_event(
            ctx,
            action=action,
            target_type="collection",
            target_id=coll_id,
            outcome="success",
            details={"parent_lens_id": parent_id} if parent_id else None,
        )
        return DocumentCollection.from_row(await _load_collection(ctx, coll_id))

    @strawberry.mutation
    async def demote_lens(self, info: Info, collection_id: strawberry.ID) -> DocumentCollection:
        """Demote a lens back to an ordinary collection (clears ``isScope``).

        Refuses for the reserved ``global`` lens, while any document still names it
        as its default lens (reassign those first), and while it still parents
        another lens (demote the children first). The collection and its
        membership are untouched — only the scope flag is cleared.

        Raises:
            ValueError: for ``global``, or while the lens is an in-use default or a
                parent of another lens — rejected before the write.
        """
        ctx = info.context
        action = "mutation.demote_lens"
        await _require_write_audited(ctx, action=action)
        coll_id = _clean_id(str(collection_id), "collection")
        row = await _load_collection(ctx, coll_id)
        if lenses.is_reserved_name(row["name"]):
            await _deny(ctx, action, coll_id, "reserved_global_lens")
            raise ValueError("the global lens cannot be demoted")
        in_use = await _count(
            ctx, "SELECT docray_lens_default_document_count(%s::uuid) AS n", (coll_id,)
        )
        if in_use > 0:
            await _deny(ctx, action, coll_id, "default_lens_in_use")
            raise ValueError(
                f"lens is still the default for {in_use} document(s); reassign them first"
            )
        children = await _count(
            ctx,
            "SELECT count(*) AS n FROM document_collections WHERE parent_lens_id = %s",
            (coll_id,),
        )
        if children > 0:
            await _deny(ctx, action, coll_id, "still_parents_a_lens")
            raise ValueError(f"lens still parents {children} lens(es); demote the children first")
        await ctx.conn.execute(
            "UPDATE document_collections SET is_scope = false WHERE id = %s", (coll_id,)
        )
        await _audit_mutation_event(
            ctx, action=action, target_type="collection", target_id=coll_id, outcome="success"
        )
        return DocumentCollection.from_row(await _load_collection(ctx, coll_id))

    @strawberry.mutation
    async def set_document_default_lens(
        self,
        info: Info,
        document_id: strawberry.ID,
        lens_id: strawberry.ID | None = None,
    ) -> Document:
        """Set (or clear) a document's default lens.

        The lens must be a scope collection the document is a member of; a null
        ``lensId`` clears the default. Requires a write grant on the document
        (per-document ACL), not merely global write scope.

        Raises:
            ValueError: an unknown/non-scope lens, or a lens the document does not
                belong to — rejected before the write (the database re-checks).
        """
        ctx = info.context
        action = "mutation.set_document_default_lens"
        doc_id = str(document_id)
        await _require_document_write(ctx, doc_id, action=action)
        target: str | None = None
        if lens_id is not None:
            target = _clean_id(str(lens_id), "lens")
            lens_row = await read.get_collection(ctx.conn, target)
            if lens_row is None or not lens_row.get("is_scope"):
                await _deny(ctx, action, doc_id, "not_a_lens", target, kind="document")
                raise ValueError(f"{target} is not a lens")
            cur = await ctx.conn.execute(
                "SELECT 1 FROM document_collection_members "
                "WHERE collection_id = %s AND document_id = %s",
                (target, doc_id),
            )
            if await cur.fetchone() is None:
                await _deny(ctx, action, doc_id, "not_a_member", target, kind="document")
                raise ValueError("document is not a member of that lens")
        await ctx.conn.execute(
            "UPDATE documents SET default_lens_id = %s WHERE id = %s", (target, doc_id)
        )
        await _audit_mutation_event(
            ctx,
            action=action,
            target_type="document",
            target_id=doc_id,
            outcome="success",
            details={"default_lens_id": target},
        )
        row = await read.get_document(ctx.conn, doc_id)
        if row is None:  # pragma: no cover - the row was just written under a write grant
            raise ValueError(f"unknown document {doc_id}")
        return Document.from_row(row)
