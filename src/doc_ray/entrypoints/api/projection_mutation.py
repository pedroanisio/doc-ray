"""GraphQL mutations for the projection engine (issue #8).

Three writes, mixed into the ``Mutation`` root (D-015):

* ``confirmProjection`` / ``rejectProjection`` — a human review of one mention's
  lens-scoped identity. The API records the decision into ``entity_projection_review``
  (a table it may write) keyed by the mention's STABLE span, then enqueues a refresh
  that applies it onto the batch-owned ``entity_projection`` (the NER-correction log's record-then-
  apply shape — the API never writes the derived fact table in a request, FR-1/D-3).
  The decision survives re-annotation because it is keyed by span, not mention id.
* ``setLensProjects`` — designate (or un-designate) a lens as projecting (FR-2). A
  curation decision, so it is write-audited on success and denial.

All three are guarded (write scope + billing entitlement + — for the reviews —
per-document ACL) and audited.
"""

from __future__ import annotations

import re
from typing import Any

import strawberry

from doc_ray.adapters import persistence_projection_read as projection_read
from doc_ray.adapters import persistence_read as read
from doc_ray.domain.jobs import KIND_REFRESH_PROJECTION
from doc_ray.domain.ulid import ULID_REGEX
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.ids import clean_uuid as _clean_uuid
from doc_ray.entrypoints.api.mutation_guards import (
    _audit_mutation_event,
    _enqueue_job,
    _job_status,
    _require_document_write,
    _require_write_audited,
)
from doc_ray.entrypoints.api.types import DocumentCollection, Info, Job


async def _resolve_review_target(
    ctx: RequestContext, mention_id: strawberry.ID, lens_id: strawberry.ID
) -> tuple[str, str, dict[str, Any]]:
    """Validate the ids and resolve the mention's document + stable span (no guard)."""
    # A mention id is a ULID (entity_mention.id), not a UUID; the lens id is a UUID.
    raw_mention = str(mention_id).strip().upper()
    raw_lens = _clean_uuid(str(lens_id))
    if not re.fullmatch(ULID_REGEX, raw_mention) or raw_lens is None:
        raise ValueError("mentionId and lensId must be valid ids")
    span = await projection_read.mention_span(ctx.conn, raw_mention)
    if span is None:
        raise ValueError(f"unknown mention {mention_id}")
    return str(span["document_id"]), raw_lens, span


async def _apply_review(
    ctx: RequestContext,
    document_id: str,
    lens_id: str,
    span: dict[str, Any],
    decision: str,
    action: str,
) -> Job:
    """Record the review keyed by the stable span, enqueue its application, and audit it."""
    await projection_read.record_projection_review(
        ctx.conn,
        document_id=document_id,
        lens_id=lens_id,
        char_start=int(span["char_start"]),
        char_end=int(span["char_end"]),
        entity_type=str(span["entity_type"]),
        decision=decision,
        reviewer=ctx.principal.principal_id,
        rationale=None,
    )
    job_id = await _enqueue_job(
        ctx, KIND_REFRESH_PROJECTION, {"lens_id": lens_id}, document_id=document_id
    )
    await _audit_mutation_event(
        ctx,
        action=action,
        target_type="entity_projection",
        target_id=document_id,
        outcome="success",
        details={"lens_id": lens_id, "decision": decision, "job_id": job_id},
    )
    return await _job_status(ctx, job_id)


@strawberry.type
class ProjectionMutations:
    """Human review of lens-scoped identity, and lens projection designation."""

    @strawberry.mutation
    async def confirm_projection(
        self, info: Info, mention_id: strawberry.ID, lens_id: strawberry.ID
    ) -> Job:
        """Confirm a mention's projection within a lens — the verification step (#8).

        Records the decision keyed by the mention's stable span (so it survives
        re-annotation) and enqueues a refresh that promotes the projection to
        ``status='confirmed'``. This is how a proposed identity becomes a decided one
        (PURPOSE.md — "human direction over autonomous drift"). Returns the queued job.
        """
        ctx = info.context
        action = "mutation.confirm_projection"
        document_id, raw_lens, span = await _resolve_review_target(ctx, mention_id, lens_id)
        await _require_document_write(ctx, document_id, action=action)
        return await _apply_review(ctx, document_id, raw_lens, span, "confirmed", action)

    @strawberry.mutation
    async def reject_projection(
        self, info: Info, mention_id: strawberry.ID, lens_id: strawberry.ID
    ) -> Job:
        """Reject a mention's projection within a lens (#8).

        The mirror of ``confirmProjection``: records a ``rejected`` decision (keyed by
        the stable span) and enqueues the refresh that applies it. The substrate is
        untouched — only the derived projection's review status changes. Returns the job.
        """
        ctx = info.context
        action = "mutation.reject_projection"
        document_id, raw_lens, span = await _resolve_review_target(ctx, mention_id, lens_id)
        await _require_document_write(ctx, document_id, action=action)
        return await _apply_review(ctx, document_id, raw_lens, span, "rejected", action)

    @strawberry.mutation
    async def set_lens_projects(
        self, info: Info, lens_id: strawberry.ID, projects: bool
    ) -> DocumentCollection:
        """Designate a lens as projecting (or stop it projecting) (FR-2, #8).

        Only *designated* lenses are materialized by the projection engine — the cost
        control that keeps projecting 10k documents viable. The lens must already be a
        scope (promote the collection first); the database re-checks
        (``chk_collection_projects_requires_scope``). A curation decision: audited on
        success and denial.
        """
        ctx = info.context
        action = "mutation.set_lens_projects"
        await _require_write_audited(ctx, action=action)
        raw_lens = _clean_uuid(str(lens_id))
        if raw_lens is None:
            raise ValueError(f"unknown lens {lens_id}")
        row = await read.get_collection(ctx.conn, raw_lens)
        if row is None:
            raise ValueError(f"unknown lens {lens_id}")
        if projects and not row.get("is_scope"):
            await _audit_mutation_event(
                ctx,
                action=action,
                target_type="collection",
                target_id=raw_lens,
                outcome="denied",
                details={"reason": "not_a_lens"},
            )
            raise ValueError(f"collection {lens_id} is not a lens (promote it first)")
        await ctx.conn.execute(
            "UPDATE document_collections SET projects = %s WHERE id = %s", (projects, raw_lens)
        )
        await _audit_mutation_event(
            ctx,
            action=action,
            target_type="collection",
            target_id=raw_lens,
            outcome="success",
            details={"projects": projects},
        )
        updated = await read.get_collection(ctx.conn, raw_lens)
        assert updated is not None  # just updated above
        return DocumentCollection.from_row(updated)
