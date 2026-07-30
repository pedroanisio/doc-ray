"""Human decision mutation for untrusted Tier-3 visual-caption proposals."""

from __future__ import annotations

import strawberry

from doc_ray.adapters.persistence_visual_write import (
    decide_visual_captions,
    retract_visual_caption,
)
from doc_ray.entrypoints.api.context import MAX_BATCH_IDS
from doc_ray.entrypoints.api.mutation_guards import (
    _audit_mutation_event,
    _require_write_audited,
)
from doc_ray.entrypoints.api.types import Info


@strawberry.type
class VisualReviewMutations:
    """Commit or reject visual captions only after an explicit decision."""

    @strawberry.mutation
    async def decide_visual_captions(
        self,
        info: Info,
        approve_ids: list[strawberry.ID] | None = None,
        reject_ids: list[strawberry.ID] | None = None,
    ) -> int:
        """Approve trusted proposals or reject them; never auto-decides."""
        ctx = info.context
        action = "mutation.decide_visual_captions"
        await _require_write_audited(ctx, action=action)
        approve = list(dict.fromkeys(str(value) for value in (approve_ids or [])))
        reject = list(dict.fromkeys(str(value) for value in (reject_ids or [])))
        if len(approve) + len(reject) > MAX_BATCH_IDS:
            raise ValueError(f"batch size exceeds {MAX_BATCH_IDS}")
        if set(approve) & set(reject):
            raise ValueError("a visual review id cannot be both approved and rejected")
        reviewer_id = ctx.principal.principal_id
        if reviewer_id is None:  # guarded above; keeps the adapter contract explicit
            raise PermissionError("visual caption decisions require an authenticated reviewer")
        decided = await decide_visual_captions(
            ctx.conn,
            approve_ids=approve,
            reject_ids=reject,
            reviewer_id=reviewer_id,
        )
        await _audit_mutation_event(
            ctx,
            action=action,
            target_type="visual_review",
            target_id=None,
            outcome="success",
            details={
                "approve_requested": len(approve),
                "reject_requested": len(reject),
                "rows_decided": decided,
            },
        )
        return decided

    @strawberry.mutation
    async def retract_visual_caption(
        self,
        info: Info,
        caption_id: strawberry.ID,
        reason: str,
    ) -> int:
        """Retract a published caption a reader can see is wrong.

        Captions are committed on write and shown in the reader with their model
        and grounding score (BL-201), so the recorded human verification event is
        this retraction rather than an approval. It is attributed to the calling
        principal, timestamped, and keeps the operator's ``reason`` — the row is
        never deleted, because it is the audit trail for both the model call and
        the human decision about it.

        Returns the number of rows retracted: ``0`` when the caption does not
        exist or was not committed, so a caller cannot read a no-op as success.
        """
        ctx = info.context
        action = "mutation.retract_visual_caption"
        await _require_write_audited(ctx, action=action)
        principal_id = ctx.principal.principal_id
        if principal_id is None:  # guarded above; keeps the adapter contract explicit
            raise PermissionError("retracting a caption requires an authenticated principal")
        retracted = await retract_visual_caption(
            ctx.conn,
            caption_id=str(caption_id),
            principal_id=principal_id,
            reason=reason,
        )
        await _audit_mutation_event(
            ctx,
            action=action,
            target_type="visual_caption",
            target_id=str(caption_id),
            # A request that matched nothing still COMPLETED; "noop" is not an
            # allowed audit outcome (only success/failure/denied). The
            # rows_retracted detail is what distinguishes the two cases.
            outcome="success",
            details={"rows_retracted": retracted, "reason": reason.strip()[:200]},
        )
        return retracted
