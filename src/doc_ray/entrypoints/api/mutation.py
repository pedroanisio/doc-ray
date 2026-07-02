"""The GraphQL ``Mutation`` root — enqueue background jobs (D-004, D-005).

Writes never happen in the request: each mutation validates, (for uploads) stores
the blob, then enqueues a job and returns its status. The synchronous worker does
the heavy, blocking work. Clients poll ``job(id)`` (or a future subscription) for
completion.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import strawberry
from starlette.datastructures import UploadFile
from strawberry.file_uploads import Upload

from doc_ray.adapters import queue_postgres as queue
from doc_ray.adapters import seed_suggestion_store as seeds
from doc_ray.domain.jobs import (
    KIND_DELETE,
    KIND_INGEST,
    KIND_REANNOTATE,
    KIND_SEED_APPROVED,
    KIND_SEED_SUGGEST,
)
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.types import Info, Job


async def _job_status(ctx: RequestContext, job_id: str) -> Job:
    """Load the freshly-enqueued job's status row and map it to a GraphQL Job."""
    row = await queue.status_async(ctx.conn, job_id)
    if row is None:  # pragma: no cover - the row was just inserted
        raise RuntimeError(f"enqueued job {job_id} vanished")
    return Job.from_row(row)


@strawberry.type
class Mutation:
    """Write operations, all of which enqueue background work."""

    @strawberry.mutation
    async def reannotate(self, info: Info, document_id: strawberry.ID, lang: str = "en") -> Job:
        """Enqueue a re-annotation of a document; returns the queued job."""
        ctx = info.context
        job_id = await queue.enqueue_async(
            ctx.conn, KIND_REANNOTATE, {"lang": lang}, document_id=str(document_id)
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def delete_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue deletion of a document (cascades to its layers); returns the job."""
        ctx = info.context
        job_id = await queue.enqueue_async(ctx.conn, KIND_DELETE, {}, document_id=str(document_id))
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def suggest_seeds(self, info: Info, document_id: strawberry.ID, limit: int = 40) -> Job:
        """Enqueue mining + KB resolution of seed candidates for a document.

        Results land in ``seedSuggestions`` as ``pending`` rows for curation;
        nothing reaches the canonical registry without an approval.
        """
        ctx = info.context
        job_id = await queue.enqueue_async(
            ctx.conn, KIND_SEED_SUGGEST, {"limit": limit}, document_id=str(document_id)
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def decide_seeds(
        self,
        info: Info,
        approve_ids: list[strawberry.ID] | None = None,
        reject_ids: list[strawberry.ID] | None = None,
    ) -> int:
        """Record curator decisions on suggestions; returns rows updated.

        ``seeded`` rows are immutable — re-deciding them is a no-op.
        """
        conn = info.context.conn
        updated = 0
        if approve_ids:
            updated += await seeds.decide_async(conn, [str(i) for i in approve_ids], "approved")
        if reject_ids:
            updated += await seeds.decide_async(conn, [str(i) for i in reject_ids], "rejected")
        return updated

    @strawberry.mutation
    async def seed_approved(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue seeding of a document's approved suggestions into the registry."""
        ctx = info.context
        job_id = await queue.enqueue_async(
            ctx.conn, KIND_SEED_APPROVED, {}, document_id=str(document_id)
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def ingest_document(self, info: Info, file: Upload, title: str | None = None) -> Job:
        """Upload a document: store the blob, enqueue ingestion; returns the job.

        The blob is stored content-addressed (``sha[:2]/sha.ext``) before enqueuing
        so the worker can read it back; the object store's ``put`` (blocking boto3)
        runs off the event loop.
        """
        import anyio

        from doc_ray.adapters.object_store import ObjectStore
        from doc_ray.config import Config

        ctx = info.context
        # The Upload scalar resolves to a Starlette UploadFile at runtime.
        upload = cast(UploadFile, file)
        data = await upload.read()
        sha = hashlib.sha256(data).hexdigest()
        filename = upload.filename
        ext = Path(filename).suffix if filename else ""
        key = f"{sha[:2]}/{sha}{ext}"

        store = ObjectStore(Config.from_env())
        await anyio.to_thread.run_sync(store.put, key, data, "application/octet-stream")

        payload = {
            "storage_key": key,
            "sha256": sha,
            "extension": ext,
            "original_filename": filename,
            "title": title,
        }
        job_id = await queue.enqueue_async(ctx.conn, KIND_INGEST, payload, document_id=None)
        return await _job_status(ctx, job_id)
