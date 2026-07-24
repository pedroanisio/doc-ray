"""MCP/GraphQL write surface for human NER corrections (Block A).

Split out of :mod:`doc_ray.entrypoints.api.mutation` (D-015) and mixed into the
``Mutation`` root. One mutation: record a curator's fix to an ML-produced entity
mention. Read the log back with the ``entityMentionCorrections`` query.

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — this is the verification layer for the
NER model's output. ``entity_mention`` rows are ephemeral (CASCADE from
``annotations``), so a fix written there would vanish on the next annotate run;
corrections are therefore stored against a *stable span identity* and re-applied
by the pipeline on every pass. The API only records the decision — the
``entity_mention`` table is owned by the batch worker (docray_batch), so a cheap
``apply_corrections`` job carries the fix through.
"""

from __future__ import annotations

import strawberry

from doc_ray.adapters import ner_corrections_store
from doc_ray.domain.jobs import KIND_APPLY_CORRECTIONS
from doc_ray.domain.ner_corrections import Action, Correction
from doc_ray.entrypoints.api.mutation_guards import (
    _audit_mutation_event,
    _enqueue_job,
    _job_status,
    _require_document_write,
)
from doc_ray.entrypoints.api.types import Info, Job


@strawberry.type
class CorrectionMutations:
    """Curation writes over the NER-correction log."""

    @strawberry.mutation
    async def correct_entity_mention(
        self,
        info: Info,
        document_id: strawberry.ID,
        action: str,
        char_start: int,
        char_end: int,
        entity_type: str | None = None,
        surface: str | None = None,
        new_char_start: int | None = None,
        new_char_end: int | None = None,
        original_type: str | None = None,
        rationale: str | None = None,
    ) -> Job:
        """Record a durable human NER correction and enqueue its application (Block A).

        ``action`` is one of ``reclassify``, ``reject``, ``add``, ``adjust_span``.
        The correction is written to the append-only log keyed by the stable span
        ``(charStart, charEnd)`` so it survives every re-annotation; a cheap
        ``apply_corrections`` job then re-applies it onto the document's current ML
        mentions (``entity_mention`` is worker-owned, so the API never writes it
        directly). Returns that queued job. See ``entityMentionCorrections`` to
        read the log back.

        Raises:
            ValueError: If ``action`` is unknown or the correction is malformed
                for its action (e.g. ``reclassify`` without an ``entityType``) —
                rejected before anything is recorded (PALS's Law: the input is
                validated, not trusted).
        """
        ctx = info.context
        audit_action = "mutation.correct_entity_mention"
        await _require_document_write(ctx, str(document_id), action=audit_action)
        try:
            correction = Correction(
                action=Action(action),
                char_start=char_start,
                char_end=char_end,
                entity_type=entity_type,
                surface=surface,
                new_char_start=new_char_start,
                new_char_end=new_char_end,
            )
            correction.validate()
        except ValueError:
            await _audit_mutation_event(
                ctx,
                action=audit_action,
                target_type="entity_mention_correction",
                target_id=None,
                outcome="denied",
                details={"reason": "invalid_correction", "action": action},
            )
            raise
        correction_id = await ner_corrections_store.record_correction_async(
            ctx.conn,
            str(document_id),
            correction,
            reviewer=ctx.principal.principal_id,
            rationale=rationale,
            original_type=original_type,
        )
        job_id = await _enqueue_job(ctx, KIND_APPLY_CORRECTIONS, {}, document_id=str(document_id))
        await _audit_mutation_event(
            ctx,
            action=audit_action,
            target_type="entity_mention_correction",
            target_id=correction_id,
            outcome="success",
            details={"correction_action": action, "job_id": job_id},
        )
        return await _job_status(ctx, job_id)
