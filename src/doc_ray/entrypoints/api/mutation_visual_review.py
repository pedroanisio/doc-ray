"""Human decision mutation for untrusted Tier-3 visual-caption proposals."""

from __future__ import annotations

import strawberry

from doc_ray.adapters.persistence_visual_write import decide_visual_captions
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
