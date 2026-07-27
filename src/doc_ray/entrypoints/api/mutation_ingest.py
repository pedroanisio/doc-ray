"""The ``ingestDocument`` mutation — the corpus's two intake routes (D-015 split).

One GraphQL field, two routes: a multipart ``file`` upload, or a ``ref`` that a
registered source claims. They share a job kind and nothing else — one stores
bytes the client sent, the other hands the worker a reference to fetch — so they
live here rather than beside the reprocessing mutations.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import strawberry
from starlette.datastructures import UploadFile
from strawberry.file_uploads import Upload

from doc_ray.domain.jobs import DEFAULT_PIPELINE_CHAIN, KIND_INGEST
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.mutation_guards import (
    _enqueue_job,
    _job_status,
    _read_upload_bounded,
    _require_write_audited,
)
from doc_ray.entrypoints.api.types import Info, Job


async def _ingest_reference(
    ctx: RequestContext, reference: str, title: str | None, pipeline: bool
) -> Job:
    """Enqueue an ingest job that the worker resolves and fetches.

    The fetch belongs to the worker (a remote download must not hold a request
    open), but *resolution* belongs here: a reference no source claims can only
    ever produce a failed job, so rejecting it now turns a poll-and-discover
    into an immediate, actionable error.
    """
    from doc_ray.adapters import document_source as source_mod

    if source_mod.resolve_source(reference) is None:
        raise ValueError(
            f"no source adapter matches {reference!r} — supported references: "
            f"{', '.join(s.name for s in source_mod.SOURCES)}"
        )
    payload: dict[str, Any] = {
        "ref": reference,
        "title": title,
        # Ownership is resolved here for the same reason an upload's is: the
        # worker has no request principal to consult (migration 102).
        "owner_principal_id": ctx.principal.principal_id,
    }
    if pipeline:
        payload["chain"] = list(DEFAULT_PIPELINE_CHAIN)
    job_id = await _enqueue_job(ctx, KIND_INGEST, payload, document_id=None)
    return await _job_status(ctx, job_id)


@strawberry.type
class IngestMutations:
    """The document-intake mutation, mixed into the ``Mutation`` root."""

    @strawberry.mutation
    async def ingest_document(
        self,
        info: Info,
        file: Upload | None = None,
        ref: str | None = None,
        title: str | None = None,
        pipeline: bool = True,
    ) -> Job:
        """Ingest a document from an upload **or** a remote reference; returns the job.

        Exactly one intake route per call:

        * ``file`` — a multipart upload. The blob is stored content-addressed
          before the job is enqueued, so the worker can read it back.
        * ``ref`` — a reference a registered source claims (``adapters.
          document_source``), e.g. ``https://arxiv.org/abs/2401.00001``,
          ``arxiv:2401.00001``, or a bare arXiv id. The *worker* fetches it, so
          this call never blocks on a remote download; the reference is
          validated here so an unresolvable one fails immediately instead of
          becoming a queued job that could only fail.

        Supplying both, or neither, is a caller error.

        With ``pipeline`` (the default) the ingest job carries the standard
        chain — annotate, then deterministic canonicalize — so an intake flows
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
        await _require_write_audited(ctx, action="mutation.ingest")
        settings = Config.from_env()

        reference = (ref or "").strip()
        if bool(file) == bool(reference):
            raise ValueError(
                "ingestDocument needs exactly one of 'file' (an upload) or "
                "'ref' (a remote reference) — not both, not neither."
            )
        if reference:
            return await _ingest_reference(ctx, reference, title, pipeline)

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
            # Ownership is resolved here, not in the worker: this is the only
            # point where a request principal exists (migration 102). An upload
            # therefore lands in the uploader's own corpus.
            "owner_principal_id": ctx.principal.principal_id,
        }
        if pipeline:
            payload["chain"] = list(DEFAULT_PIPELINE_CHAIN)
        job_id = await _enqueue_job(ctx, KIND_INGEST, payload, document_id=None)
        return await _job_status(ctx, job_id)
