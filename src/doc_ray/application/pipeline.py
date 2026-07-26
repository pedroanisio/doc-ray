"""End-to-end ingest: read bytes -> hash -> store blob -> extract -> persist row."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from doc_ray.adapters import extraction as extract_mod
from doc_ray.adapters import persistence as db
from doc_ray.adapters.object_store import ObjectStore
from doc_ray.config import Config
from doc_ray.domain import provenance as prov_mod
from doc_ray.domain import sentences as sent_mod
from doc_ray.domain.jobs import DEFAULT_PIPELINE_CHAIN
from doc_ray.domain.progress import report_progress


@dataclass
class IngestResult:
    """Outcome of ingesting a single document.

    Attributes:
        path: Label of what was ingested — a filesystem path for file intake,
            the remote reference for source intake (:func:`ingest_refs`).
        document_id: Id of the stored document, if one was created.
        status: One of ``'ingested'``, ``'duplicate'`` or ``'failed'``.
        detail: Optional human-readable detail (counts or error text).
    """

    path: str
    document_id: str | None
    status: str
    detail: str = ""


def _sha256(data: bytes) -> str:
    """Return the hex SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def ingest_bytes(
    conn: psycopg.Connection[Any],
    store: ObjectStore,
    data: bytes,
    *,
    filename: str,
    title: str | None = None,
    source_url: str | None = None,
    owner_principal_id: str | None = None,
) -> IngestResult:
    """Ingest raw bytes: dedupe, store the blob, extract, and persist.

    The bytes-first core every intake path shares — local files
    (:func:`ingest_file`), queued uploads (``handle_ingest``), and remote
    sources (:func:`ingest_refs`) all land here.

    Args:
        conn: Live database connection.
        store: Object store for the original bytes.
        data: Raw document bytes.
        filename: Safe basename (with extension); drives extractor dispatch
            and the fallback title. Never a caller-controlled path.
        title: Authoritative title override (e.g. from the arXiv Atom API).
            ``None`` keeps the extractor-derived title.
        source_url: Where the bytes came from, recorded in the document's
            ``metadata`` for provenance; also labels the result.
        owner_principal_id: Principal the resulting document belongs to
            (migration 102). An authenticated upload carries the uploader here
            so the document lands in *their* corpus; operator CLI ingest leaves
            it ``None`` (unowned — admin-only until org corpora ship).

    Returns:
        An ``IngestResult`` describing the outcome (ingested, duplicate, or
        failed).
    """
    label = source_url or filename
    name = Path(filename).name
    stem = Path(name).stem
    ext = Path(name).suffix
    sha = _sha256(data)

    # Dedupe: identical bytes are skipped.
    report_progress(1, 5, "Checking duplicate content")
    existing = db.find_by_sha256(conn, sha)
    if existing:
        if existing.get("status") == "failed":
            # These exact bytes are already recorded as a *failed* document, so
            # this is not a duplicate of anything usable — and re-extraction can
            # never run, because this very lookup short-circuits it. Reporting
            # "duplicate" made a permanent failure read as a benign no-op: the
            # Spaces loader marked such objects terminal-OK and exited 0 while
            # the documents sat in status='failed'. Re-surface the failure.
            detail = str(existing.get("error") or "previously failed to extract")
            return IngestResult(label, str(existing["id"]), "failed", detail)
        return IngestResult(label, str(existing["id"]), "duplicate")

    # Content-addressed key keeps the bucket flat and collision-free.
    key = f"{sha[:2]}/{sha}{ext}"

    def record_failure(error: str) -> IngestResult:
        """Store the blob and a FAILED document row carrying *error*."""
        uri = store.put(key, data, "application/octet-stream")
        provenance = prov_mod.build_events(
            content_sha256=sha,
            source_filename=name,
            byte_size=len(data),
            storage_bucket=store.bucket,
            storage_key=key,
            storage_uri=uri,
            status="failed",
            error=error,
        )
        doc_id = db.insert_document(
            conn,
            title=title or stem,
            doc_type=ext.lstrip(".") or "unknown",
            original_filename=name,
            source_uri=uri,
            content_sha256=sha,
            byte_size=len(data),
            page_count=None,
            full_text=None,
            status="failed",
            error=error,
            provenance=provenance,
            owner_principal_id=owner_principal_id,
        )
        return IngestResult(label, doc_id, "failed", error)

    try:
        report_progress(2, 5, "Extracting document text")
        extracted = extract_mod.extract(data, filename=stem, extension=ext)
    except Exception as exc:  # extraction failed -> still record the blob + failure
        return record_failure(str(exc))

    if not extracted.full_text.strip():
        # A file with nothing to read must fail loudly, not become an empty
        # document that "succeeds" through the whole pipeline (live incident:
        # a 368-page scanned PDF with zero text layer).
        return record_failure(
            "no extractable text — scanned/image-only file? OCR required before ingest"
        )

    report_progress(3, 5, "Storing original blob")
    uri = store.put(key, data, extracted.content_type)

    full_text = extracted.full_text or ""
    text_sha = _sha256(full_text.encode("utf-8"))
    sentences = sent_mod.build_sentences(full_text)
    report_progress(4, 5, "Persisting extracted document")
    provenance = prov_mod.build_events(
        content_sha256=sha,
        source_filename=name,
        byte_size=len(data),
        storage_bucket=store.bucket,
        storage_key=key,
        storage_uri=uri,
        status="extracted",
        extractor=extracted.extractor,
        extractor_version=extracted.extractor_version,
        page_count=extracted.page_count,
        char_count=len(full_text),
        text_sha256=text_sha,
        sentence_count=len(sentences),
    )
    metadata = dict(extracted.metadata)  # {pdf_producer, pdf_creator, ...}
    if source_url:
        metadata.setdefault("source_url", source_url)
    doc_id = db.insert_document(
        conn,
        title=title or extracted.title,
        doc_type=extracted.doc_type,
        mime_type=extracted.content_type,
        original_filename=name,
        source_uri=uri,
        content_sha256=sha,
        byte_size=len(data),
        page_count=extracted.page_count,
        char_count=len(full_text),
        text_sha256=text_sha,
        full_text=extracted.full_text,
        status="extracted",
        authors=[extracted.author] if extracted.author else [],
        metadata=metadata,
        sentences=sentences,
        provenance=provenance,
        page_runs=extracted.page_runs,
        page_metrics=getattr(extracted, "page_metrics", None),
        owner_principal_id=owner_principal_id,
    )
    return IngestResult(
        label,
        doc_id,
        "ingested",
        f"{len(sentences)} sentences, {len(provenance)} prov",
    )


def ingest_file(
    conn: psycopg.Connection[Any],
    store: ObjectStore,
    path: Path,
    owner_principal_id: str | None = None,
) -> IngestResult:
    """Ingest a single file: read bytes and delegate to :func:`ingest_bytes`.

    Args:
        conn: Live database connection.
        store: Object store for the original bytes.
        path: File to ingest.
        owner_principal_id: See :func:`ingest_bytes`.

    Returns:
        An ``IngestResult`` labeled with the filesystem *path*.
    """
    report_progress(0, 5, "Reading file")
    result = ingest_bytes(
        conn,
        store,
        path.read_bytes(),
        filename=path.name,
        owner_principal_id=owner_principal_id,
    )
    result.path = str(path)
    return result


def ingest_paths(
    cfg: Config,
    paths: list[Path],
    *,
    chain: Sequence[str] | None = DEFAULT_PIPELINE_CHAIN,
) -> list[IngestResult]:
    """Ingest every path, sharing one object store and database connection.

    Every *new* document is handed to the default post-ingest pipeline and gets
    its read-model rollup refreshed, exactly like a queued API upload
    (``handle_ingest``) — ``DEFAULT_PIPELINE_CHAIN`` is "the pipeline every
    ingest path owes a document". Before this, the CLI/bulk path stopped at
    text + sentences, leaving the document with ``sentenceCount`` 0 and no
    annotation layers, no failed job, and no error anywhere (the silent
    no-layer state, BL-162). Duplicates and failures never enter the chain —
    the same guard the Spaces loader applies.

    Args:
        cfg: Runtime configuration.
        paths: Files to ingest.
        chain: Post-ingest job kinds to enqueue for each new document; pass
            ``None`` (or ``()``) to opt out of the pipeline entirely.

    Returns:
        One ``IngestResult`` per input path, in order. A chained document's
        ``detail`` names the queued head job (``", queued annotate"``).
    """
    store = ObjectStore(cfg)
    store.ensure_bucket()
    results: list[IngestResult] = []
    with db.connect(cfg) as conn:
        for p in paths:
            result = ingest_file(conn, store, p)
            finalize_new_document(conn, result, chain)
            results.append(result)
    return results


def ingest_refs(
    cfg: Config,
    refs: list[str],
    *,
    chain: Sequence[str] | None = DEFAULT_PIPELINE_CHAIN,
) -> list[IngestResult]:
    """Fetch every remote reference through its source adapter and ingest it.

    The remote mirror of :func:`ingest_paths`: each ref is resolved against the
    source registry (``adapters.document_source``), fetched, and run through
    :func:`ingest_bytes` with the source's title and URL. New documents owe the
    same post-ingest chain as every other intake path (BL-162); fetch failures
    become ``failed`` results without a document row — there are no bytes to
    keep for forensics.

    Args:
        cfg: Runtime configuration.
        refs: Remote references (e.g. arXiv URLs/ids).
        chain: Post-ingest job kinds per new document; ``None``/``()`` opts out.

    Returns:
        One ``IngestResult`` per ref, in order, labeled by the ref itself.
    """
    # Imported lazily like the job machinery: sources pull in httpx, which
    # this module must not require at import time.
    from doc_ray.adapters import document_source as source_mod

    store = ObjectStore(cfg)
    store.ensure_bucket()
    results: list[IngestResult] = []
    with db.connect(cfg) as conn:
        for ref in refs:
            source = source_mod.resolve_source(ref)
            if source is None:
                results.append(IngestResult(ref, None, "failed", "no source adapter matches"))
                continue
            try:
                fetched = source.fetch(ref)
            except Exception as exc:
                results.append(IngestResult(ref, None, "failed", f"{source.name}: {exc}"))
                continue
            result = ingest_bytes(
                conn,
                store,
                fetched.data,
                filename=fetched.filename,
                title=fetched.title,
                source_url=fetched.source_url,
            )
            result.path = ref
            finalize_new_document(conn, result, chain)
            results.append(result)
    return results


def finalize_new_document(
    conn: psycopg.Connection[Any],
    result: IngestResult,
    chain: Sequence[str] | None,
) -> str | None:
    """Refresh stats and enqueue the post-ingest chain for a *new* document.

    Duplicates and failures never enter the chain — the guard every ingest
    path shares (BL-162). Public because "what a new document owes" must have
    exactly one definition: the out-of-package bulk loaders
    (``scripts/ingest_from_spaces.py``) call this rather than re-deriving it,
    which is how the Spaces loader came to chain its documents without ever
    refreshing their read-model rollup.

    Args:
        conn: Live database connection.
        result: The ingest outcome; its ``detail`` gains the queued head job.
        chain: Post-ingest job kinds; ``None``/``()`` enqueues nothing.

    Returns:
        The id of the enqueued head job, or ``None`` when nothing was queued.
    """
    # Imported lazily: the job machinery pulls in the queue adapter and the
    # handler registry, which this module must not require at import time
    # (job_handlers imports this module's ingest_bytes the same lazy way).
    from doc_ray.application.document_stats import refresh_document_stats
    from doc_ray.application.jobs import enqueue_chain_head

    if result.status != "ingested" or result.document_id is None:
        return None
    refresh_document_stats(conn, result.document_id)
    if not chain:
        return None
    job_id = enqueue_chain_head(conn, result.document_id, chain)
    if job_id:
        result.detail += f", queued {chain[0]}"
    return job_id


def expand_paths(inputs: list[str], extensions: set[str]) -> list[Path]:
    """Expand files and directories into a flat list of supported files.

    Args:
        inputs: File or directory paths as strings.
        extensions: Lowercased extensions (including the dot) to accept.

    Returns:
        A flat list of matching files (directories searched recursively).
    """
    out: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix.lower() in extensions:
                    out.append(child)
        elif p.is_file():
            out.append(p)
    return out
