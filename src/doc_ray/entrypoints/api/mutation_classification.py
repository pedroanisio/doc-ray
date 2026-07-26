"""GraphQL mutations for document genre classification (SLP Block C, issue #6).

Two writes, both guarded (write scope + billing entitlement + per-document ACL)
and audited (including the denied path):

* ``classifyDocument`` enqueues the heuristic classifier job — the worker writes a
  ``pending`` machine guess to ``document_classification`` (docray_api may not
  write the machine row; only docray_batch does, so the API enqueues).
* ``confirmDocumentClassification`` records a human decision directly
  (``status='confirmed'``, ``method='human'``) — the verification step that turns
  a guess into a decided genre. This overrides any prior value and is the only way
  a genre is asserted (PALS's Law).
"""

from __future__ import annotations

import strawberry

from doc_ray.adapters import document_classification_store
from doc_ray.domain.genre_policy import Genre
from doc_ray.domain.jobs import KIND_CLASSIFY_GENRE
from doc_ray.entrypoints.api.mutation_guards import (
    _audit_mutation_event,
    _enqueue_job,
    _job_status,
    _require_document_write,
)
from doc_ray.entrypoints.api.types import DocumentClassification, Info, Job


@strawberry.type
class ClassificationMutations:
    """Genre-classification writes over a document the caller may write."""

    @strawberry.mutation
    async def classify_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue a heuristic genre classification for a document (Block C, #6).

        The job writes a ``pending`` machine guess to ``document_classification``
        when it runs. A ``pending`` classification is NOT a decided genre — it is an
        unverified guess; confirm it with ``confirmDocumentClassification``. The job
        never overwrites a human-confirmed row. Returns the queued job.
        """
        ctx = info.context
        audit_action = "mutation.classify_document"
        await _require_document_write(ctx, str(document_id), action=audit_action)
        job_id = await _enqueue_job(ctx, KIND_CLASSIFY_GENRE, {}, document_id=str(document_id))
        await _audit_mutation_event(
            ctx,
            action=audit_action,
            target_type="document_classification",
            target_id=str(document_id),
            outcome="success",
            details={"job_id": job_id},
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def confirm_document_classification(
        self,
        info: Info,
        document_id: strawberry.ID,
        genre: str,
        subtype: str | None = None,
        rationale: str | None = None,
    ) -> DocumentClassification:
        """Record a human genre confirmation (``status='confirmed'``, ``method='human'``).

        The verification decision (PURPOSE.md — "human direction over autonomous
        drift"): it overrides any prior machine or human value, records the reviewer
        and rationale, and is the only path that asserts a genre as decided.
        ``genre`` must be one of ``fiction``, ``non_fiction``, ``mixed``, ``unknown``.
        """
        ctx = info.context
        audit_action = "mutation.confirm_document_classification"
        await _require_document_write(ctx, str(document_id), action=audit_action)
        try:
            parsed = Genre(genre)
        except ValueError:
            await _audit_mutation_event(
                ctx,
                action=audit_action,
                target_type="document_classification",
                target_id=str(document_id),
                outcome="denied",
                details={"reason": "invalid_genre", "genre": genre},
            )
            raise ValueError(
                f"invalid genre {genre!r}; expected one of {[g.value for g in Genre]}"
            ) from None
        await document_classification_store.confirm_classification_async(
            ctx.conn,
            str(document_id),
            genre=parsed,
            subtype=subtype,
            reviewer=ctx.principal.principal_id,
            rationale=rationale,
        )
        await _audit_mutation_event(
            ctx,
            action=audit_action,
            target_type="document_classification",
            target_id=str(document_id),
            outcome="success",
            details={"genre": parsed.value},
        )
        row = await document_classification_store.get_classification_async(
            ctx.conn, str(document_id)
        )
        assert row is not None  # just written above
        return DocumentClassification.from_row(row)
