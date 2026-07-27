"""Remote-reference intake for the ``ingest`` job kind (D-015 split).

Kept beside :mod:`doc_ray.application.job_handlers` rather than inside it: the
upload route and the reference route share a job kind but nothing else — one
reads a blob the API already stored, the other performs a network fetch.
"""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.adapters.object_store import ObjectStore as S3ObjectStore
from doc_ray.domain.jobs import Job
from doc_ray.domain.progress import report_progress


def ingest_reference(
    conn: psycopg.Connection[Any], store: S3ObjectStore, job: Job
) -> dict[str, Any]:
    """Fetch a referenced document through its source adapter, then ingest it.

    The queued mirror of :func:`doc_ray.application.pipeline.ingest_refs`. A
    fetch failure raises rather than returning a ``failed`` result: there are no
    bytes to record for forensics, so the queue's own error/retry record is the
    only honest trail — and a transient network fault deserves the retry that a
    recorded failure would deny it.
    """
    from doc_ray.adapters import document_source as source_mod
    from doc_ray.application.document_stats import refresh_document_stats
    from doc_ray.application.pipeline import ingest_bytes

    ref = str(job.payload["ref"])
    report_progress(0, 3, "Resolving source")
    source = source_mod.resolve_source(ref)
    if source is None:
        raise RuntimeError(f"no source adapter matches {ref!r}")
    report_progress(1, 3, f"Fetching from {source.name}")
    fetched = source.fetch(ref)
    report_progress(2, 3, "Extracting and storing document")
    owner = job.payload.get("owner_principal_id")
    title = job.payload.get("title")
    result = ingest_bytes(
        conn,
        store,
        fetched.data,
        filename=fetched.filename,
        title=str(title) if title else fetched.title,
        source_url=fetched.source_url,
        owner_principal_id=str(owner) if owner else None,
    )
    if result.status == "ingested" and result.document_id is not None:
        refresh_document_stats(conn, result.document_id)
    return {"document_id": result.document_id, "status": result.status}
