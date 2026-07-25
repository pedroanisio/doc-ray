"""Layer 20 semantic-embedding mutations, mixed into the ``Mutation`` root.

Split out of :mod:`doc_ray.entrypoints.api.mutation` (D-015); ``SemanticMutations``
is a Strawberry type the root ``Mutation`` inherits. Both mutations enqueue the
heavy, worker-only ``semantic_embed`` job (sentence-transformers stack required);
each is billing-guarded like every other paid write.
"""

from __future__ import annotations

import strawberry

from doc_ray.application.semantic_embed import enqueue_semantic_embed_jobs
from doc_ray.domain.jobs import KIND_SEMANTIC_EMBED
from doc_ray.entrypoints.api.mutation_guards import (
    _audit_mutation_event,
    _enqueue_job,
    _job_status,
    _require_admin,
    _require_document_write,
)
from doc_ray.entrypoints.api.types import Info, Job


@strawberry.type
class SemanticMutations:
    """Layer-20 learned sentence-embedding writes, inherited by the root ``Mutation``."""

    @strawberry.mutation
    async def embed_document_sentences(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue a learned sentence-embedding (NLP Layer 20) run; returns the job.

        Heavy, worker-only: requires the sentence-transformers stack. The job is
        never auto-chained — embedding cost stays an explicit request. Once it
        completes, ``semanticSearchSentences`` can retrieve against the vectors.
        """
        ctx = info.context
        await _require_document_write(
            ctx, str(document_id), action="mutation.embed_document_sentences"
        )
        job_id = await _enqueue_job(ctx, KIND_SEMANTIC_EMBED, {}, document_id=str(document_id))
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def embed_corpus_sentences(self, info: Info, only_missing: bool = True) -> list[Job]:
        """Enqueue learned sentence-embedding (Layer 20) jobs across the corpus; admin-only.

        The one-shot re-embed for the hard cutover: with ``only_missing`` (default),
        it skips documents that already carry vectors for the active model, so it is
        safely re-runnable. The heavy embedding runs on the worker (sentence stack
        required); each job is billing-guarded like every other paid write.
        """
        ctx = info.context
        await _require_admin(ctx, action="mutation.embed_corpus_sentences")

        async def enqueue_document(document_id: str) -> str:
            return await _enqueue_job(ctx, KIND_SEMANTIC_EMBED, {}, document_id=document_id)

        job_ids = await enqueue_semantic_embed_jobs(
            ctx.conn,
            only_missing=only_missing,
            enqueue_document=enqueue_document,
        )
        jobs = [await _job_status(ctx, job_id) for job_id in job_ids]
        if not jobs:
            await _audit_mutation_event(
                ctx,
                action="mutation.embed_corpus_sentences",
                target_type="corpus",
                target_id=None,
                outcome="success",
                details={"jobs_enqueued": 0},
            )
        return jobs
