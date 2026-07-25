"""Layer 20 semantic-embedding mutations, mixed into the ``Mutation`` root.

Split out of :mod:`doc_ray.entrypoints.api.mutation` (D-015); ``SemanticMutations``
is a Strawberry type the root ``Mutation`` inherits. Both mutations enqueue the
heavy, worker-only ``semantic_embed`` job (sentence-transformers stack required);
each is billing-guarded like every other paid write.
"""

from __future__ import annotations

import strawberry

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
        from doc_ray.adapters.text_encoder_sentence_transformers import DEFAULT_MODEL_TAG

        ctx = info.context
        await _require_admin(ctx, action="mutation.embed_corpus_sentences")
        missing_clause = (
            "AND NOT EXISTS ("
            " SELECT 1 FROM embedding e JOIN sentences s2 ON s2.span_id = e.span_id"
            " WHERE s2.document_id = d.id AND e.model = %(model)s)"
            if only_missing
            else ""
        )
        cur = await ctx.conn.execute(
            f"""
            SELECT d.id
            FROM documents d
            WHERE EXISTS (SELECT 1 FROM sentences s WHERE s.document_id = d.id)
              {missing_clause}
            ORDER BY d.created_at, d.id
            """,
            {"model": DEFAULT_MODEL_TAG},
        )
        rows = await cur.fetchall()
        jobs: list[Job] = []
        for row in rows:
            job_id = await _enqueue_job(ctx, KIND_SEMANTIC_EMBED, {}, document_id=str(row["id"]))
            jobs.append(await _job_status(ctx, job_id))
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
