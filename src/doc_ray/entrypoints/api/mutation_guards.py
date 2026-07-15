"""Authorization guards and enqueue plumbing shared by every GraphQL mutation.

Split out of :mod:`doc_ray.entrypoints.api.mutation` (D-015). Names keep their
original leading underscores: they are the module-private helpers of the
mutation surface, referenced by the D-019 decision checks, not a public API.
"""

from __future__ import annotations

import uuid
from typing import Any

import strawberry
from starlette.datastructures import UploadFile

from doc_ray.adapters import abac
from doc_ray.adapters import queue_postgres as queue
from doc_ray.application import billing
from doc_ray.correlation import payload_with_request_metadata
from doc_ray.entrypoints.api.context import MAX_BATCH_IDS, RequestContext
from doc_ray.entrypoints.api.security_audit import record_audit_event
from doc_ray.entrypoints.api.types import Job

_BILLING_ENTITLEMENT_EXEMPT_ACTIONS = {
    "mutation.create_billing_checkout_session",
    "mutation.create_billing_portal_session",
    "query.billing_status",
}


async def _audit_mutation_event(
    ctx: RequestContext,
    *,
    action: str,
    target_type: str,
    target_id: str | None,
    outcome: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Record one mutation outcome to the append-only security audit log."""
    await record_audit_event(
        ctx.conn,
        principal=ctx.principal,
        action=action,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        details=details,
    )


def _has_write_scope(ctx: RequestContext) -> bool:
    """Return true when the context principal may attempt write mutations."""
    return ctx.principal.principal_id is not None and "write" in ctx.principal.scopes


def _require_write(ctx: RequestContext) -> None:
    """Require an authenticated principal with write scope for API mutations."""
    if not _has_write_scope(ctx):
        raise PermissionError("API mutation requires an authenticated principal with write scope")


async def _require_write_audited(ctx: RequestContext, *, action: str) -> None:
    """Require write scope and record permission-denied attempts."""
    if ctx.principal.principal_id is None or "write" not in ctx.principal.scopes:
        await _audit_mutation_event(
            ctx,
            action=action,
            target_type="mutation",
            target_id=None,
            outcome="denied",
        )
        raise PermissionError("API mutation requires an authenticated principal with write scope")
    if action not in _BILLING_ENTITLEMENT_EXEMPT_ACTIONS:
        try:
            await billing.require_entitlement(ctx.conn, ctx.settings)
        except billing.BillingEntitlementError as exc:
            await _audit_mutation_event(
                ctx,
                action=action,
                target_type="tenant_billing",
                target_id=None,
                outcome="denied",
                details={"reason": "billing_entitlement_required"},
            )
            raise PermissionError(str(exc)) from exc


async def _require_document_write(
    ctx: RequestContext, document_id: str, *, action: str = "mutation.document.write"
) -> None:
    """Require a write grant on *document_id* specifically (BL-064).

    Global write scope (:func:`_require_write`) is necessary but not
    sufficient: a principal scoped to one document's ``document_acl`` must
    not be able to mutate another. ``jobs`` also enforces this at the RLS
    layer (migration 043) so a bypass here is not the only backstop, but
    checking first turns a raw RLS policy violation into a clean, expected
    error and avoids the failed INSERT entirely. The same error is raised
    whether the document doesn't exist, merely isn't in the caller's grants, or
    is malformed, so none of those can be distinguished by the caller (BL-110:
    a bare ``%s::uuid`` cast raises Postgres's raw input-syntax error on a
    malformed id — validate in Python first so that never reaches the client).
    """
    await _require_write_audited(ctx, action=action)
    try:
        uuid.UUID(document_id)
    except ValueError as exc:
        await _audit_mutation_event(
            ctx,
            action=action,
            target_type="document",
            target_id=document_id,
            outcome="denied",
            details={"reason": "malformed_document_id"},
        )
        raise PermissionError(f"no write grant for document {document_id}") from exc
    cur = await ctx.conn.execute(
        "SELECT %s::uuid IN (SELECT * FROM docray_writable_documents()) AS ok",
        (document_id,),
    )
    row = await cur.fetchone()
    if not row or not row["ok"]:
        await _audit_mutation_event(
            ctx,
            action=action,
            target_type="document",
            target_id=document_id,
            outcome="denied",
            details={"reason": "missing_document_write_grant"},
        )
        raise PermissionError(f"no write grant for document {document_id}")
    try:
        await abac.require_document_write_policy(ctx.conn, document_id)
    except PermissionError as exc:
        await _audit_mutation_event(
            ctx,
            action=action,
            target_type="document",
            target_id=document_id,
            outcome="denied",
            details={"reason": "abac_policy_denied", "policy_error": str(exc)},
        )
        raise


async def _require_admin(ctx: RequestContext, *, action: str = "mutation.admin") -> None:
    """Require an admin principal, for corpus-wide mutations (backup/restore).

    Backup/restore affect the whole corpus, not one document, so there is no
    ``document_acl`` grant that makes sense as a scope check — only admins may
    call these (BL-064). ``jobs``/``backups`` RLS (migration 043) is the
    backstop; this turns a raw policy violation into a clean error.
    """
    await _require_write_audited(ctx, action=action)
    cur = await ctx.conn.execute("SELECT docray_is_admin() AS ok")
    row = await cur.fetchone()
    if not row or not row["ok"]:
        await _audit_mutation_event(
            ctx,
            action=action,
            target_type="corpus",
            target_id=None,
            outcome="denied",
            details={"reason": "admin_required"},
        )
        raise PermissionError("this mutation requires an admin principal")


_UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _read_upload_bounded(upload: UploadFile, max_bytes: int) -> bytes:
    """Read *upload* in bounded chunks, rejecting it once it exceeds *max_bytes* (BL-098).

    Reading in chunks — rather than a single ``upload.read()`` — means an
    oversized upload is rejected before its full body is ever buffered in
    process memory, regardless of whether the client sent an accurate
    Content-Length header.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"upload exceeds the {max_bytes}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def _job_status(ctx: RequestContext, job_id: str) -> Job:
    """Load the freshly-enqueued job's status row and map it to a GraphQL Job."""
    row = await queue.status_async(ctx.conn, job_id)
    if row is None:  # pragma: no cover - the row was just inserted
        raise RuntimeError(f"enqueued job {job_id} vanished")
    document_id = row.get("document_id")
    await _audit_mutation_event(
        ctx,
        action=f"mutation.{row['kind']}",
        target_type="document" if document_id is not None else "job",
        target_id=str(document_id) if document_id is not None else str(job_id),
        outcome="success",
        details={"job_id": str(job_id)},
    )
    return Job.from_row(row)


async def _enqueue_job(
    ctx: RequestContext,
    kind: str,
    payload: dict[str, Any],
    *,
    priority: int = 100,
    max_attempts: int = 3,
    document_id: str | None = None,
) -> str:
    """Enqueue a job with this GraphQL request's correlation metadata."""
    return await queue.enqueue_async(
        ctx.conn,
        kind,
        payload_with_request_metadata(payload, ctx.request_id),
        priority=priority,
        max_attempts=max_attempts,
        document_id=document_id,
    )


def _clean_bulk_ids(document_ids: list[strawberry.ID]) -> list[str]:
    """Return unique document ids in caller order, enforcing the batch ceiling."""
    ids = [str(document_id) for document_id in document_ids]
    if len(ids) > MAX_BATCH_IDS:
        raise ValueError(f"batch size exceeds {MAX_BATCH_IDS}")
    return list(dict.fromkeys(ids))
