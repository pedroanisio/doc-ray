"""The GraphQL ``Mutation`` root — enqueue background jobs (D-004, D-005).

Writes never happen in the request: each mutation validates, (for uploads) stores
the blob, then enqueues a job and returns its status. The synchronous worker does
the heavy, blocking work. Clients poll ``job(id)`` (or a future subscription) for
completion.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, cast

import strawberry
from starlette.datastructures import UploadFile
from strawberry.file_uploads import Upload

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
    KIND_INGEST,
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
from doc_ray.entrypoints.api.context import MAX_BATCH_IDS, RequestContext
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
    whether the document doesn't exist, merely isn't in the caller's grants, or
    is malformed, so none of those can be distinguished by the caller (BL-110:
    a bare ``%s::uuid`` cast raises Postgres's raw input-syntax error on a
    malformed id — validate in Python first so that never reaches the client).
    """
    _require_write(ctx)
    try:
        uuid.UUID(document_id)
    except ValueError as exc:
        raise PermissionError(f"no write grant for document {document_id}") from exc
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


_UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _read_upload_bounded(upload: UploadFile, max_bytes: int) -> bytes:
    """Read *upload* in bounded chunks, rejecting it once it exceeds *max_bytes* (BL-098).

    Reading in chunks — rather than a single ``upload.read()`` — means an
    oversized upload is rejected before its full body is ever buffered in
    process memory, regardless of whether the client sent an accurate
    Content-Length header.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"upload exceeds the {max_bytes}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def _job_status(ctx: RequestContext, job_id: str) -> Job:
    """Load the freshly-enqueued job's status row and map it to a GraphQL Job."""
    row = await queue.status_async(ctx.conn, job_id)
    if row is None:  # pragma: no cover - the row was just inserted
        raise RuntimeError(f"enqueued job {job_id} vanished")
    return Job.from_row(row)


def _clean_bulk_ids(document_ids: list[strawberry.ID]) -> list[str]:
    """Return unique document ids in caller order, enforcing the batch ceiling."""
    ids = [str(document_id) for document_id in document_ids]
    if len(ids) > MAX_BATCH_IDS:
        raise ValueError(f"batch size exceeds {MAX_BATCH_IDS}")
    return list(dict.fromkeys(ids))


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
    async def reextract_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue PDF geometry backfill for one document; returns the queued job."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id))
        job_id = await queue.enqueue_async(
            ctx.conn, KIND_REEXTRACT, {}, document_id=str(document_id)
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def reextract_documents(
        self, info: Info, only_missing_geometry: bool = True
    ) -> list[Job]:
        """Enqueue PDF geometry backfill jobs for the corpus; admin-only."""
        ctx = info.context
        await _require_admin(ctx)
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
            job_id = await queue.enqueue_async(
                ctx.conn, KIND_REEXTRACT, {}, document_id=str(row["id"])
            )
            jobs.append(await _job_status(ctx, job_id))
        return jobs

    @strawberry.mutation
    async def delete_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue a recoverable soft delete of a document; returns the job."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id))
        job_id = await queue.enqueue_async(
            ctx.conn,
            KIND_DELETE,
            {"actor": ctx.principal.principal_id},
            document_id=str(document_id),
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def archive_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue archival of a document without changing ingestion status."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id))
        job_id = await queue.enqueue_async(
            ctx.conn,
            KIND_ARCHIVE_DOCUMENT,
            {"actor": ctx.principal.principal_id},
            document_id=str(document_id),
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def restore_document(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue restoring an archived or soft-deleted document to active."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id))
        job_id = await queue.enqueue_async(
            ctx.conn,
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
            await _require_document_write(ctx, document_id)
            job_id = await queue.enqueue_async(
                ctx.conn,
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
            await _require_document_write(ctx, document_id)
            job_id = await queue.enqueue_async(
                ctx.conn,
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
    ) -> Job:
        """Enqueue an edit to a document's title/description/tags; returns the job.

        Only the fields passed are changed; each actually-changed field is
        recorded in ``documentMetadataHistory``. Tags, when passed, fully
        replace the document's current tag set. Requires a write grant on the
        document itself, like every other document-scoped mutation (BL-064).
        """
        ctx = info.context
        await _require_document_write(ctx, str(document_id))
        payload: dict[str, Any] = {"actor": ctx.principal.principal_id}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if tags is not None:
            payload["tags"] = list(tags)
        job_id = await queue.enqueue_async(
            ctx.conn, KIND_UPDATE_METADATA, payload, document_id=str(document_id)
        )
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
            ctx.conn,
            KIND_SEED_SUGGEST,
            {"limit": ctx.page_size(limit)},
            document_id=str(document_id),
        )
        return await _job_status(ctx, job_id)

    @strawberry.mutation
    async def suggest_document_metadata(self, info: Info, document_id: strawberry.ID) -> Job:
        """Enqueue verified title/tag/duplicate suggestions for one document."""
        ctx = info.context
        await _require_document_write(ctx, str(document_id))
        job_id = await queue.enqueue_async(
            ctx.conn,
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
        _require_write(ctx)
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
        _require_write(ctx)
        for name, ids in (("approveIds", approve_ids), ("rejectIds", reject_ids)):
            if ids is not None and len(ids) > MAX_BATCH_IDS:
                raise ValueError(f"{name} cannot exceed {MAX_BATCH_IDS} ids per call")
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
        the event loop. The upload is capped at ``DOCRAY_MAX_UPLOAD_MB`` (BL-098),
        the same ceiling the webapp's Flask route already enforces.
        """
        import anyio

        from doc_ray.adapters import extraction
        from doc_ray.adapters.object_store import ObjectStore
        from doc_ray.config import Config

        ctx = info.context
        _require_write(ctx)
        settings = Config.from_env()
        # The Upload scalar resolves to a Starlette UploadFile at runtime.
        upload = cast(UploadFile, file)
        data = await _read_upload_bounded(upload, settings.max_upload_bytes)
        sha = hashlib.sha256(data).hexdigest()
        filename = upload.filename
        ext = Path(filename).suffix if filename else ""
        # BL-106: the extension is attacker-supplied; check it against the same
        # allowlist extraction.py dispatches on *before* the blob ever reaches
        # the object store, not after (extraction's own check ran too late).
        if not extraction.is_supported_extension(ext):
            raise ValueError(f"unsupported document type: {ext!r}")
        key = f"{sha[:2]}/{sha}{ext}"

        store = ObjectStore(settings)
        content_type = extraction.content_type_for_extension(ext)
        await anyio.to_thread.run_sync(store.put, key, data, content_type)

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
