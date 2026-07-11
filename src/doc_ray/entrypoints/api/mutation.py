"""The GraphQL ``Mutation`` root — enqueue background jobs (D-004, D-005).

Writes never happen in the request: each mutation validates, (for uploads) stores
the blob, then enqueues a job and returns its status. The synchronous worker does
the heavy, blocking work. Clients poll ``job(id)`` (or a future subscription) for
completion.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import strawberry
from starlette.datastructures import UploadFile
from strawberry.file_uploads import Upload

from doc_ray.adapters import queue_postgres as queue
from doc_ray.adapters import seed_suggestion_store as seeds
from doc_ray.domain.jobs import (
    KIND_ANNOTATE,
    KIND_BACKUP,
    KIND_CANONICALIZE,
    KIND_DELETE,
    KIND_INGEST,
    KIND_REANNOTATE,
    KIND_RESTORE,
    KIND_SEED_APPROVED,
    KIND_SEED_SUGGEST,
    KIND_SEMANTIC_ENRICH,
)
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.types import Info, Job

#: The default post-ingest pipeline: Stage-1 annotation, deterministic semantic
#: enrichment, then deterministic canonicalization. The frontier resolver is never
#: chained — LLM cost stays an explicit request (D-018).
_PIPELINE_CHAIN = [KIND_ANNOTATE, KIND_SEMANTIC_ENRICH, KIND_CANONICALIZE]


def _require_write(ctx: RequestContext) -> None:
    """Require an authenticated principal with write scope for API mutations."""
    if ctx.principal.principal_id is None or "write" not in ctx.principal.scopes:
        raise PermissionError("API mutation requires an authenticated principal with write scope")


async def _require_document_write(ctx: RequestContext, document_id: str) -> None:
    """Require a write grant on *document_id* specifically (BL-064).

    Global write scope (:func:`_require_write`) is necessary but not
    sufficient: a principal scoped to one document's ``document_acl`` must
    not be able to mutate another. ``jobs`` also enforces this at the RLS
    layer (migration 043) so a bypass here is not the only backstop, but
    checking first turns a raw RLS policy violation into a clean, expected
    error and avoids the failed INSERT entirely. The same error is raised
    whether the document doesn't exist or merely isn't in the caller's
    grants, so this can't be used to enumerate documents the caller can't see.
    """
    _require_write(ctx)
    cur = await ctx.conn.execute(
        "SELECT %s::uuid IN (SELECT * FROM docray_writable_documents()) AS ok",
        (document_id,),
    )
    row = await cur.fetchone()
    if not row or not row["ok"]:
        raise PermissionError(f"no write grant for document {document_id}")


async def _require_admin(ctx: RequestContext) -> None:
    """Require an admin principal, for corpus-wide mutations (backup/restore).

    Backup/restore affect the whole corpus, not one document, so there is no
    ``document_acl`` grant that makes sense as a scope check — only admins may
    call these (BL-064). ``jobs``/``backups`` RLS (migration 043) is the
    backstop; this turns a raw policy violation into a clean error.
    """
    _require_write(ctx)
    cur = await ctx.conn.execute("SELECT docray_is_admin() AS ok")
    row = await cur.fetchone()
    if not row or not row["ok"]:
        raise PermissionError("this mutation requires an admin principal")


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
        await _require_document_write(ctx, str(document_id))
        job_id = await queue.enqueue_async(
            ctx.conn, KIND_REANNOTATE, {"lang": lang}, document_id=str(document_id)
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def delete_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue deletion of a document (cascades to its layers); returns the job."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id))
        job_id = await queue.enqueue_async(ctx.conn, KIND_DELETE, {}, document_id=str(document_id))
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def annotate_document(
        self, info: Info, document_id: strawberry.ID, lang: str = "en"
    ) -> Job:
        """Enqueue Stage-1 annotation (tokens/NER/coref) for a document."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id))
        job_id = await queue.enqueue_async(
            ctx.conn, KIND_ANNOTATE, {"lang": lang}, document_id=str(document_id)
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def semantic_enrich_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue deterministic semantic enrichment for a document."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id))
        job_id = await queue.enqueue_async(
            ctx.conn, KIND_SEMANTIC_ENRICH, {}, document_id=str(document_id)
        )
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
        await _require_document_write(ctx, str(document_id))
        payload = {"resolve": True} if resolve else {}
        job_id = await queue.enqueue_async(
            ctx.conn, KIND_CANONICALIZE, payload, document_id=str(document_id)
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def suggest_seeds(self, info: Info, document_id: strawberry.ID, limit: int = 40) -> Job:
        """Enqueue mining + KB resolution of seed candidates for a document.

        Results land in ``seedSuggestions`` as ``pending`` rows for curation;
        nothing reaches the canonical registry without an approval.
        """
        ctx = info.context
        await _require_document_write(ctx, str(document_id))
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
        ctx = info.context
        _require_write(ctx)
        conn = ctx.conn
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
        await _require_document_write(ctx, str(document_id))
        job_id = await queue.enqueue_async(
            ctx.conn, KIND_SEED_APPROVED, {}, document_id=str(document_id)
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def create_backup(self, info: Info, note: str | None = None) -> Job:
        """Enqueue a full backup (DB dump + object-store blobs); returns the job.

        The worker writes a ``.tar.gz`` archive to the object store and indexes it
        in the ``backups`` table. Poll ``job(id)`` for completion.
        """
        ctx = info.context
        await _require_admin(ctx)
        payload = {"note": note} if note else {}
        job_id = await queue.enqueue_async(ctx.conn, KIND_BACKUP, payload, document_id=None)
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def restore_backup(self, info: Info, backup_id: strawberry.ID) -> Job:
        """Enqueue a restore of the whole corpus from a catalogued backup.

        DESTRUCTIVE: the worker rewrites every corpus table and re-uploads blobs
        from the archive. The target must be at the archive's schema version.
        """
        ctx = info.context
        await _require_admin(ctx)
        job_id = await queue.enqueue_async(
            ctx.conn, KIND_RESTORE, {"backup_id": str(backup_id)}, document_id=None
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def ingest_document(
        self, info: Info, file: Upload, title: str | None = None, pipeline: bool = True
    ) -> Job:
        """Upload a document: store the blob, enqueue ingestion; returns the job.

        With ``pipeline`` (the default) the ingest job carries the standard
        chain — annotate, then deterministic canonicalize — so an upload flows
        to a fully linked document with no further calls. The blob is stored
        content-addressed (``sha[:2]/sha.ext``) before enqueuing so the worker
        can read it back; the object store's ``put`` (blocking boto3) runs off
        the event loop.
        """
        import anyio

        from doc_ray.adapters.object_store import ObjectStore
        from doc_ray.config import Config

        ctx = info.context
        _require_write(ctx)
        # The Upload scalar resolves to a Starlette UploadFile at runtime.
        upload = cast(UploadFile, file)
        data = await upload.read()
        sha = hashlib.sha256(data).hexdigest()
        filename = upload.filename
        ext = Path(filename).suffix if filename else ""
        key = f"{sha[:2]}/{sha}{ext}"

        store = ObjectStore(Config.from_env())
        await anyio.to_thread.run_sync(store.put, key, data, "application/octet-stream")

        payload: dict[str, Any] = {
            "storage_key": key,
            "sha256": sha,
            "extension": ext,
            "original_filename": filename,
            "title": title,
        }
        if pipeline:
            payload["chain"] = list(_PIPELINE_CHAIN)
        job_id = await queue.enqueue_async(ctx.conn, KIND_INGEST, payload, document_id=None)
        return await _job_status(ctx, job_id)
