"""Owner-driven document-sharing mutations (Phase 3), merged into ``Mutation``.

Split out of :mod:`doc_ray.entrypoints.api.mutation` (D-015):
``SharingMutations`` is a Strawberry type the root ``Mutation`` class inherits,
so the schema is unchanged.

Sharing grants another principal — identified by login email — ``read`` or
``annotate`` on a document the caller owns (or, for an admin, any document in
the tenant). The authorization is enforced twice: :func:`_require_document_share`
gives a clean, audited error, and ``document_acl``'s RLS policies (migration 108)
are the authoritative backstop — the write runs under the caller's own RLS
connection, so a forged grant fails at the database even if the guard is wrong.
``admin``/``redact`` are never grantable this way.
"""

from __future__ import annotations

import strawberry

from doc_ray.entrypoints.api.mutation_guards import (
    _audit_mutation_event,
    _require_document_share,
)
from doc_ray.entrypoints.api.types import DocumentShare, Info

#: Permissions a non-admin owner may hand out. ``admin``/``redact`` stay
#: admin-CLI-only; ``delete`` is deliberately excluded — sharing lends a
#: document, it does not transfer control of it.
_SHAREABLE_PERMISSIONS = ("read", "annotate")

#: Document-visibility scopes (migration 110). ``private`` = owner/ACL/admin
#: only; ``org`` = readable by every principal in the tenant.
_VISIBILITIES = ("private", "org")


async def _resolve_share_target(ctx: object, email: str) -> str:
    """Resolve *email* to a principal id in the caller's tenant, or raise.

    Uses the ``docray_principal_by_email`` SECURITY DEFINER helper (migration
    108), which is tenant-scoped, so this cannot enumerate principals outside the
    caller's tenant. The same error is raised for an unknown and an out-of-tenant
    address, so neither can be distinguished.
    """
    clean = email.strip().lower()
    if not clean or "@" not in clean:
        raise ValueError("a valid email address is required to share")
    cur = await ctx.conn.execute(  # type: ignore[attr-defined]
        "SELECT docray_principal_by_email(%s) AS id", (clean,)
    )
    row = await cur.fetchone()
    if row is None or row["id"] is None:
        raise ValueError(f"no account found for {clean}")
    return str(row["id"])


@strawberry.type
class SharingMutations:
    """Document ACL grant/revoke, mixed into the root ``Mutation``."""

    @strawberry.mutation
    async def share_document(
        self, info: Info, document_id: strawberry.ID, email: str, permission: str = "read"
    ) -> DocumentShare:
        """Grant a colleague ``read`` (default) or ``annotate`` on a document.

        The caller must own the document or be a tenant admin. Idempotent: a
        repeated grant of the same permission returns the existing row.
        """
        ctx = info.context
        clean_permission = permission.strip().lower()
        if clean_permission not in _SHAREABLE_PERMISSIONS:
            raise ValueError(
                f"permission must be one of {', '.join(_SHAREABLE_PERMISSIONS)}; "
                "admin and redact are not shareable"
            )
        await _require_document_share(ctx, str(document_id), action="mutation.share_document")
        target = await _resolve_share_target(ctx, email)
        await ctx.conn.execute(
            "INSERT INTO document_acl (document_id, principal_id, permission) "
            "VALUES (%s, %s, %s) ON CONFLICT (document_id, principal_id, permission) DO NOTHING",
            (str(document_id), target, clean_permission),
        )
        cur = await ctx.conn.execute(
            "SELECT principal_id, permission, granted_at, "
            "       docray_email_for_principal(principal_id) AS email "
            "FROM document_acl "
            "WHERE document_id = %s AND principal_id = %s AND permission = %s",
            (str(document_id), target, clean_permission),
        )
        row = await cur.fetchone()
        if row is None:  # the INSERT/ON CONFLICT guarantees the row exists
            raise RuntimeError("share insert returned no row")
        await _audit_mutation_event(
            ctx,
            action="mutation.share_document",
            target_type="document",
            target_id=str(document_id),
            outcome="success",
            details={"grantee": target, "permission": clean_permission},
        )
        return DocumentShare.from_row(row)

    @strawberry.mutation
    async def set_document_visibility(
        self, info: Info, document_id: strawberry.ID, visibility: str
    ) -> str:
        """Publish a document to the whole tenant (``org``) or make it ``private``.

        Owner-or-admin only, like sharing — publishing to the organization is a
        share-class act. The write goes through ``docray_set_document_visibility``,
        which re-checks that authority in the database, so this cannot be used to
        publish a document the caller does not own.
        """
        ctx = info.context
        clean = visibility.strip().lower()
        if clean not in _VISIBILITIES:
            raise ValueError(f"visibility must be one of {', '.join(_VISIBILITIES)}")
        await _require_document_share(
            ctx, str(document_id), action="mutation.set_document_visibility"
        )
        await ctx.conn.execute(
            "SELECT docray_set_document_visibility(%s::uuid, %s)", (str(document_id), clean)
        )
        await _audit_mutation_event(
            ctx,
            action="mutation.set_document_visibility",
            target_type="document",
            target_id=str(document_id),
            outcome="success",
            details={"visibility": clean},
        )
        return clean

    @strawberry.mutation
    async def revoke_document_share(
        self, info: Info, document_id: strawberry.ID, email: str, permission: str | None = None
    ) -> int:
        """Revoke a colleague's access; returns the number of grants removed.

        With ``permission`` omitted, every grant that principal holds on the
        document is removed. Requires the same owner/admin authority as granting.
        """
        ctx = info.context
        await _require_document_share(
            ctx, str(document_id), action="mutation.revoke_document_share"
        )
        target = await _resolve_share_target(ctx, email)
        if permission is None:
            cur = await ctx.conn.execute(
                "DELETE FROM document_acl WHERE document_id = %s AND principal_id = %s",
                (str(document_id), target),
            )
        else:
            clean_permission = permission.strip().lower()
            cur = await ctx.conn.execute(
                "DELETE FROM document_acl "
                "WHERE document_id = %s AND principal_id = %s AND permission = %s",
                (str(document_id), target, clean_permission),
            )
        removed = cur.rowcount
        await _audit_mutation_event(
            ctx,
            action="mutation.revoke_document_share",
            target_type="document",
            target_id=str(document_id),
            outcome="success",
            details={"grantee": target, "permission": permission, "removed": removed},
        )
        return removed
