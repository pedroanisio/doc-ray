"""The GraphQL ``Mutation`` root — enqueue background jobs (D-004, D-005).

Writes never happen in the request: each mutation validates, (for uploads) stores
the blob, then enqueues a job and returns its status. The synchronous worker does
the heavy, blocking work. Clients poll ``job(id)`` (or a future subscription) for
completion.
"""

from __future__ import annotations

from typing import Any

import strawberry

from doc_ray.adapters import document_metadata_suggestion_store as metadata_suggestions
from doc_ray.adapters import queue_postgres as queue
from doc_ray.adapters import seed_suggestion_store as seeds
from doc_ray.domain.jobs import (
    KIND_ADD_DOCUMENT_TAGS,
    KIND_ANNOTATE,
    KIND_ARCHIVE_DOCUMENT,
    KIND_BACKUP,
    KIND_CANONICALIZE,
    KIND_DELETE,
    KIND_REANNOTATE,
    KIND_REEXTRACT,
    KIND_RESTORE,
    KIND_RESTORE_DOCUMENT,
    KIND_SEED_APPROVED,
    KIND_SEED_SUGGEST,
    KIND_SEMANTIC_ENRICH,
    KIND_SUGGEST_DOCUMENT_METADATA,
    KIND_UPDATE_METADATA,
)
from doc_ray.entrypoints.api.allusion_mutation import AllusionMutations
from doc_ray.entrypoints.api.context import MAX_BATCH_IDS
from doc_ray.entrypoints.api.mutation_billing import BillingMutations
from doc_ray.entrypoints.api.mutation_classification import ClassificationMutations
from doc_ray.entrypoints.api.mutation_collections import CollectionMutations
from doc_ray.entrypoints.api.mutation_corrections import CorrectionMutations
from doc_ray.entrypoints.api.mutation_guards import (
    _audit_mutation_event,
    _clean_bulk_ids,
    _enqueue_job,
    _job_status,
    _require_admin,
    _require_backup_admin,
    _require_document_write,
    _require_write_audited,
)
from doc_ray.entrypoints.api.mutation_ingest import IngestMutations
from doc_ray.entrypoints.api.mutation_lenses import LensMutations
from doc_ray.entrypoints.api.mutation_semantic import SemanticMutations
from doc_ray.entrypoints.api.mutation_sharing import SharingMutations
from doc_ray.entrypoints.api.mutation_visual_review import VisualReviewMutations
from doc_ray.entrypoints.api.projection_mutation import ProjectionMutations
from doc_ray.entrypoints.api.types import Info, Job


@strawberry.type
class Mutation(
    AllusionMutations,
    BillingMutations,
    ClassificationMutations,
    CollectionMutations,
    CorrectionMutations,
    IngestMutations,
    LensMutations,
    ProjectionMutations,
    SemanticMutations,
    SharingMutations,
    VisualReviewMutations,
):
    """Write operations: most enqueue background work; sharing writes ACLs directly."""

    @strawberry.mutation
    async def reannotate(self, info: Info, document_id: strawberry.ID, lang: str = "en") -> Job:
        """Enqueue a re-annotation of a document; returns the queued job."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id), action="mutation.reannotate")
        job_id = await _enqueue_job(
            ctx, KIND_REANNOTATE, {"lang": lang}, document_id=str(document_id)
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def reextract_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue PDF geometry backfill for one document; returns the queued job."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id), action="mutation.reextract")
        job_id = await _enqueue_job(ctx, KIND_REEXTRACT, {}, document_id=str(document_id))
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def cancel_job(self, info: Info, job_id: strawberry.ID, reason: str | None = None) -> Job:
        """Cancel a queued or running job; admin-only."""
        ctx = info.context
        await _require_admin(ctx, action="mutation.cancel_job")
        row = await queue.cancel_async(ctx.conn, str(job_id), reason=reason)
        if row is None:
            raise ValueError(f"unknown job {job_id}")
        if row["status"] != "canceled":
            raise ValueError(f"job {job_id} is {row['status']} and cannot be canceled")
        await _audit_mutation_event(
            ctx,
            action="mutation.cancel_job",
            target_type="job",
            target_id=str(job_id),
            outcome="success",
        )
        return Job.from_row(row)

    @strawberry.mutation
    async def reextract_documents(
        self, info: Info, only_missing_geometry: bool = True
    ) -> list[Job]:
        """Enqueue PDF geometry backfill jobs for the corpus; admin-only."""
        ctx = info.context
        await _require_admin(ctx, action="mutation.reextract_documents")
        missing_clause = (
            "AND NOT EXISTS ("
            " SELECT 1 FROM spans sp WHERE sp.document_id = d.id AND sp.page_number IS NOT NULL"
            ")"
            if only_missing_geometry
            else ""
        )
        cur = await ctx.conn.execute(
            f"""
            SELECT d.id
            FROM documents d
            WHERE d.status = 'extracted'
              AND d.doc_type = 'pdf'
              AND d.source_uri IS NOT NULL
              {missing_clause}
            ORDER BY d.created_at, d.id
            """
        )
        rows = await cur.fetchall()
        jobs: list[Job] = []
        for row in rows:
            job_id = await _enqueue_job(ctx, KIND_REEXTRACT, {}, document_id=str(row["id"]))
            jobs.append(await _job_status(ctx, job_id))
        if not jobs:
            await _audit_mutation_event(
                ctx,
                action="mutation.reextract_documents",
                target_type="corpus",
                target_id=None,
                outcome="success",
                details={"jobs_enqueued": 0},
            )
        return jobs

    @strawberry.mutation
    async def delete_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue a recoverable soft delete of a document; returns the job."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id), action="mutation.delete")
        job_id = await _enqueue_job(
            ctx,
            KIND_DELETE,
            {"actor": ctx.principal.principal_id},
            document_id=str(document_id),
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def archive_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue archival of a document without changing ingestion status."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id), action="mutation.archive_document")
        job_id = await _enqueue_job(
            ctx,
            KIND_ARCHIVE_DOCUMENT,
            {"actor": ctx.principal.principal_id},
            document_id=str(document_id),
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def restore_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue restoring an archived or soft-deleted document to active."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id), action="mutation.restore_document")
        job_id = await _enqueue_job(
            ctx,
            KIND_RESTORE_DOCUMENT,
            {"actor": ctx.principal.principal_id},
            document_id=str(document_id),
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def bulk_archive_documents(
        self, info: Info, document_ids: list[strawberry.ID]
    ) -> list[Job]:
        """Enqueue archival jobs for multiple documents."""
        ctx = info.context
        ids = _clean_bulk_ids(document_ids)
        jobs: list[Job] = []
        for document_id in ids:
            await _require_document_write(
                ctx, document_id, action="mutation.bulk_archive_documents"
            )
            job_id = await _enqueue_job(
                ctx,
                KIND_ARCHIVE_DOCUMENT,
                {"actor": ctx.principal.principal_id},
                document_id=document_id,
            )
            jobs.append(await _job_status(ctx, job_id))
        return jobs

    @strawberry.mutation
    async def bulk_tag_documents(
        self, info: Info, document_ids: list[strawberry.ID], tags: list[str]
    ) -> list[Job]:
        """Enqueue tag-merge jobs for multiple documents."""
        clean_tags = sorted({tag.strip() for tag in tags if tag and tag.strip()})
        if not clean_tags:
            raise ValueError("at least one tag is required")
        ctx = info.context
        ids = _clean_bulk_ids(document_ids)
        jobs: list[Job] = []
        for document_id in ids:
            await _require_document_write(ctx, document_id, action="mutation.bulk_tag_documents")
            job_id = await _enqueue_job(
                ctx,
                KIND_ADD_DOCUMENT_TAGS,
                {"actor": ctx.principal.principal_id, "tags": clean_tags},
                document_id=document_id,
            )
            jobs.append(await _job_status(ctx, job_id))
        return jobs

    @strawberry.mutation
    async def update_document_metadata(
        self,
        info: Info,
        document_id: strawberry.ID,
        title: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        authors: list[str] | None = None,
    ) -> Job:
        """Enqueue an edit to a document's title/description/tags/authors; returns the job.

        Only the fields passed are changed; each actually-changed field is
        recorded in ``documentMetadataHistory``. ``tags`` and ``authors``, when
        passed, fully replace the document's current set (``authors`` keeps the
        order given — author order is meaningful). Requires a write grant on the
        document itself, like every other document-scoped mutation (BL-064).
        """
        ctx = info.context
        await _require_document_write(ctx, str(document_id), action="mutation.update_metadata")
        payload: dict[str, Any] = {"actor": ctx.principal.principal_id}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if tags is not None:
            payload["tags"] = list(tags)
        if authors is not None:
            payload["authors"] = list(authors)
        job_id = await _enqueue_job(
            ctx, KIND_UPDATE_METADATA, payload, document_id=str(document_id)
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def annotate_document(
        self, info: Info, document_id: strawberry.ID, lang: str = "en"
    ) -> Job:
        """Enqueue Stage-1 annotation (tokens/NER/coref) for a document."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id), action="mutation.annotate")
        job_id = await _enqueue_job(
            ctx, KIND_ANNOTATE, {"lang": lang}, document_id=str(document_id)
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def semantic_enrich_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue deterministic semantic enrichment for a document."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id), action="mutation.semantic_enrich")
        job_id = await _enqueue_job(ctx, KIND_SEMANTIC_ENRICH, {}, document_id=str(document_id))
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def canonicalize_document(
        self, info: Info, document_id: strawberry.ID, resolve: bool = False
    ) -> Job:
        """Enqueue canonicalization (term scan + Stage-2 auto-link) for a document.

        ``resolve: true`` additionally runs the frontier resolver on the residue —
        LLM cost is always an explicit request, never a default.
        """
        ctx = info.context
        await _require_document_write(ctx, str(document_id), action="mutation.canonicalize")
        payload = {"resolve": True} if resolve else {}
        job_id = await _enqueue_job(ctx, KIND_CANONICALIZE, payload, document_id=str(document_id))
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def suggest_seeds(self, info: Info, document_id: strawberry.ID, limit: int = 40) -> Job:
        """Enqueue mining + KB resolution of seed candidates for a document.

        Results land in ``seedSuggestions`` as ``pending`` rows for curation;
        nothing reaches the canonical registry without an approval.
        """
        ctx = info.context
        await _require_document_write(ctx, str(document_id), action="mutation.seed_suggest")
        job_id = await _enqueue_job(
            ctx,
            KIND_SEED_SUGGEST,
            {"limit": ctx.page_size(limit)},
            document_id=str(document_id),
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def suggest_document_metadata(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue verified title/tag/duplicate suggestions for one document."""
        ctx = info.context
        await _require_document_write(
            ctx, str(document_id), action="mutation.suggest_document_metadata"
        )
        job_id = await _enqueue_job(
            ctx,
            KIND_SUGGEST_DOCUMENT_METADATA,
            {},
            document_id=str(document_id),
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def decide_document_metadata_suggestions(
        self,
        info: Info,
        approve_ids: list[strawberry.ID] | None = None,
        reject_ids: list[strawberry.ID] | None = None,
    ) -> int:
        """Approve or reject verified metadata suggestions without auto-applying them."""
        ctx = info.context
        await _require_write_audited(ctx, action="mutation.decide_document_metadata_suggestions")
        for name, ids in (("approveIds", approve_ids), ("rejectIds", reject_ids)):
            if ids is not None and len(ids) > MAX_BATCH_IDS:
                raise ValueError(f"{name} cannot exceed {MAX_BATCH_IDS} ids per call")
        updated = 0
        if approve_ids:
            updated += await metadata_suggestions.decide_async(
                ctx.conn, [str(i) for i in approve_ids], "approved"
            )
        if reject_ids:
            updated += await metadata_suggestions.decide_async(
                ctx.conn, [str(i) for i in reject_ids], "rejected"
            )
        await _audit_mutation_event(
            ctx,
            action="mutation.decide_document_metadata_suggestions",
            target_type="document_metadata_suggestion",
            target_id=None,
            outcome="success",
            details={"rows_updated": updated},
        )
        return updated

    @strawberry.mutation
    async def decide_seeds(
        self,
        info: Info,
        approve_ids: list[strawberry.ID] | None = None,
        reject_ids: list[strawberry.ID] | None = None,
    ) -> int:
        """Record curator decisions on suggestions; returns rows updated.

        ``seeded`` rows are immutable — re-deciding them is a no-op.

        Raises:
            ValueError: If ``approveIds`` or ``rejectIds`` exceeds
                :data:`MAX_BATCH_IDS` — rejected outright rather than silently
                truncated, so a caller never has some of its decisions dropped.
        """
        ctx = info.context
        await _require_write_audited(ctx, action="mutation.decide_seeds")
        for name, ids in (("approveIds", approve_ids), ("rejectIds", reject_ids)):
            if ids is not None and len(ids) > MAX_BATCH_IDS:
                raise ValueError(f"{name} cannot exceed {MAX_BATCH_IDS} ids per call")
        conn = ctx.conn
        updated = 0
        if approve_ids:
            updated += await seeds.decide_async(conn, [str(i) for i in approve_ids], "approved")
        if reject_ids:
            updated += await seeds.decide_async(conn, [str(i) for i in reject_ids], "rejected")
        await _audit_mutation_event(
            ctx,
            action="mutation.decide_seeds",
            target_type="seed_suggestion",
            target_id=None,
            outcome="success",
            details={"rows_updated": updated},
        )
        return updated

    @strawberry.mutation
    async def seed_approved(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue seeding of a document's approved suggestions into the registry."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id), action="mutation.seed_approved")
        job_id = await _enqueue_job(ctx, KIND_SEED_APPROVED, {}, document_id=str(document_id))
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def create_backup(self, info: Info, note: str | None = None) -> Job:
        """Enqueue a full backup (DB dump + object-store blobs); returns the job.

        The worker writes a ``.tar.gz`` archive to the object store and indexes it
        in the ``backups`` table. Poll ``job(id)`` for completion.

        Requires ``DOCRAY_BACKUP_ENABLED``; the subsystem is opt-in and off by
        default (see ``Query.backupEnabled`` to check before offering the action).
        """
        ctx = info.context
        await _require_backup_admin(ctx, action="mutation.backup")
        payload = {"note": note} if note else {}
        job_id = await _enqueue_job(ctx, KIND_BACKUP, payload, document_id=None)
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def restore_backup(self, info: Info, backup_id: strawberry.ID) -> Job:
        """Enqueue a restore of the whole corpus from a catalogued backup.

        DESTRUCTIVE: the worker rewrites every corpus table and re-uploads blobs
        from the archive. The target must be at the archive's schema version.

        Requires ``DOCRAY_BACKUP_ENABLED``; the subsystem is opt-in and off by
        default (see ``Query.backupEnabled`` to check before offering the action).
        """
        ctx = info.context
        await _require_backup_admin(ctx, action="mutation.restore")
        job_id = await _enqueue_job(
            ctx, KIND_RESTORE, {"backup_id": str(backup_id)}, document_id=None
        )
        return await _job_status(ctx, job_id)
