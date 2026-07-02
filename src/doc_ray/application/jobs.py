"""Job dispatch: claim a job and route it to a handler (D-005).

The worker is a synchronous process; this module holds the kind→handler dispatch
table and the single claim→run→finalize step (:func:`run_once`). Handlers lazily
import the blocking machinery (spaCy for reannotation, the ingest pipeline) so this
module stays importable — and :func:`run_once` stays unit-testable with a stub
registry — without those heavy dependencies present.
"""

from __future__ import annotations

import contextlib
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import psycopg

from doc_ray.adapters import queue_postgres as queue
from doc_ray.domain.jobs import (
    KIND_ANNOTATE,
    KIND_CANONICALIZE,
    KIND_DELETE,
    KIND_INGEST,
    KIND_REANNOTATE,
    KIND_SEED_CANONICAL,
    Job,
)
from doc_ray.ports.object_store import ObjectStore

if TYPE_CHECKING:
    # Concrete adapter type for the (path-based) pipeline, imported for typing only
    # so this module stays importable without boto3.
    from doc_ray.adapters.object_store import ObjectStore as S3ObjectStore

#: A handler runs one job and returns a JSON-able result recorded on the job row.
Handler = Callable[[psycopg.Connection[Any], ObjectStore | None, Job], dict[str, Any]]

_BACKOFF_BASE_SECONDS = 5.0
_BACKOFF_CAP_SECONDS = 300.0


def backoff_seconds(attempts: int) -> float:
    """Return the exponential backoff (capped) for the *attempts*-th failure."""
    return float(min(_BACKOFF_BASE_SECONDS * 2 ** max(0, attempts - 1), _BACKOFF_CAP_SECONDS))


def handle_delete(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Delete a document (cascading to all its derived layers)."""
    conn.execute("DELETE FROM documents WHERE id = %s", (job.document_id,))
    return {"deleted": job.document_id}


def handle_reannotate(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Re-run linguistic annotation for a document (loads spaCy lazily)."""
    from doc_ray.application import reannotate as reann

    if job.document_id is None:
        raise RuntimeError("reannotate requires a document_id")
    lang = job.payload.get("lang", "en")
    info = reann.reannotate_document(conn, job.document_id, cache={}, lang_default=lang)
    return info or {"skipped": True}


def handle_ingest(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Ingest an uploaded blob: fetch bytes, run the path-based pipeline.

    The blob is materialised under the payload's ``original_filename`` (basename
    only — never a caller-controlled path) so the pipeline derives the document's
    title/filename from the *uploaded* name, not a random temp name. An explicit
    payload ``title`` overrides whatever the extractor derived.
    """
    from doc_ray.application.pipeline import ingest_file

    if store is None:
        raise RuntimeError("ingest requires an object store")
    data = store.get(job.payload["storage_key"])
    original = job.payload.get("original_filename") or f"upload{job.payload.get('extension', '')}"
    safe_name = Path(str(original)).name or "upload"
    # pipeline.ingest_file is path-based; materialise the blob under its real name so
    # the proven pipeline is reused unchanged (its re-PUT is content-addressed/idempotent).
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / safe_name
        tmp_path.write_bytes(data)
        # store is the concrete S3 adapter at runtime; the pipeline is typed against it.
        result = ingest_file(conn, cast("S3ObjectStore", store), tmp_path)

    title = job.payload.get("title")
    if title and result.status == "ingested" and result.document_id is not None:
        conn.execute(
            "UPDATE documents SET title = %s WHERE id = %s", (str(title), result.document_id)
        )
    return {"document_id": result.document_id, "status": result.status}


def handle_annotate(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Run Stage-1 spaCy annotation (tokens/NER/deps/coref) for one document.

    This is the worker-invocable form of the ``annotate_spacy`` CLI — required so
    the deployed worker can produce the ``entity_mention`` rows the
    canonicalization stages consume (spaCy loads lazily; ~sm model per language).
    """
    from doc_ray.adapters.annotation_spacy import SpacyAnnotator

    if job.document_id is None:
        raise RuntimeError("annotate requires a document_id")
    return SpacyAnnotator().annotate(
        conn, job.document_id, lang_default=str(job.payload.get("lang", "en"))
    )


def handle_seed_canonical(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Seed the canonical registry from Wikidata (payload: ``qids`` and/or ``terms``)."""
    from doc_ray.adapters.wikidata import WikidataClient
    from doc_ray.application import canonical_seed

    kb = WikidataClient()
    try:
        ids: list[int] = []
        if job.payload.get("qids"):
            ids += canonical_seed.seed_from_ids(conn, kb, list(job.payload["qids"]))
        if job.payload.get("terms"):
            ids += canonical_seed.seed_from_terms(conn, kb, list(job.payload["terms"]))
    finally:
        kb.close()
    return {"seeded": len(ids)}


def handle_canonicalize(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Canonicalize a document's mentions: term scan + Stage-2 auto-link, then (opt-in) Stage 3.

    The registry term scan (Stage 1.5) runs first so technical vocabulary the
    registry knows — which NER never tags — becomes linkable mentions. Then the
    frontier resolver runs only when the payload sets ``resolve`` truthy —
    LLM cost is an explicit request, never a side effect. It processes only the
    Stage-2 residue (index-first, D-019) and its verdicts are validated by the
    adapter (PALS): uncommitted ones land in ``resolution_review``.
    """
    from doc_ray.application import canonicalize, term_scan

    if job.document_id is None:
        raise RuntimeError("canonicalize requires a document_id")
    terms = term_scan.scan_document(conn, job.document_id)
    result = canonicalize.autolink_document(conn, job.document_id)
    out: dict[str, Any] = {
        "term_mentions": terms,
        "linked": result.linked,
        "residue": len(result.residue),
    }
    if job.payload.get("resolve") and result.residue:
        from doc_ray.adapters.resolver_claude import ClaudeResolver
        from doc_ray.application.resolution import resolve_residue

        outcome = resolve_residue(conn, ClaudeResolver(), job.document_id, result.residue)
        out.update(
            {
                "resolved": outcome.linked + outcome.created,
                "created": outcome.created,
                "review": outcome.review,
            }
        )
    return out


HANDLERS: dict[str, Handler] = {
    KIND_ANNOTATE: handle_annotate,
    KIND_REANNOTATE: handle_reannotate,
    KIND_DELETE: handle_delete,
    KIND_INGEST: handle_ingest,
    KIND_SEED_CANONICAL: handle_seed_canonical,
    KIND_CANONICALIZE: handle_canonicalize,
}


def _start_heartbeat(
    conn: psycopg.Connection[Any], job: Job, worker_id: str, interval: float
) -> Callable[[], None]:
    """Refresh *job*'s heartbeat on a daemon thread; return a stop() callable.

    Uses a dedicated connection (never the one the handler is using — psycopg
    connections are not safe for concurrent use).
    """
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(interval):
            with contextlib.suppress(Exception):
                queue.heartbeat(conn, job.id, worker_id=worker_id)

    thread = threading.Thread(target=_beat, daemon=True)
    thread.start()

    def _stop() -> None:
        stop.set()
        thread.join(timeout=1.0)

    return _stop


def run_once(
    conn: psycopg.Connection[Any],
    store: ObjectStore | None,
    *,
    worker_id: str,
    handlers: dict[str, Handler] | None = None,
    heartbeat_conn: psycopg.Connection[Any] | None = None,
    heartbeat_interval: float = 15.0,
) -> Job | None:
    """Claim one job, dispatch it, and finalize it; return the job (or ``None``).

    An unknown kind is a poison message → failed immediately (no retry). Any handler
    exception fails the job, requeuing with exponential backoff until the attempt
    ceiling is reached, after which it is terminal. When *heartbeat_conn* is given,
    the claimed job's heartbeat is refreshed on a side thread for the handler's
    duration (so a long job is not reaped mid-run).

    Args:
        conn: Sync database connection (autocommit).
        store: Object store for blob handlers, or ``None`` when unused.
        worker_id: Identifier recorded as the claim holder.
        handlers: Dispatch table; defaults to :data:`HANDLERS`.
        heartbeat_conn: Dedicated connection for the heartbeat thread, or ``None``.
        heartbeat_interval: Seconds between heartbeats.

    Returns:
        The claimed :class:`Job`, or ``None`` if the queue was empty.
    """
    registry = handlers if handlers is not None else HANDLERS
    job = queue.claim(conn, worker_id=worker_id)
    if job is None:
        return None
    handler = registry.get(job.kind)
    if handler is None:
        queue.fail(conn, job.id, error=f"unknown job kind {job.kind!r}", retry_in_seconds=None)
        return job
    stop_heartbeat = (
        _start_heartbeat(heartbeat_conn, job, worker_id, heartbeat_interval)
        if heartbeat_conn is not None
        else None
    )
    try:
        result = handler(conn, store, job)
        queue.complete(conn, job.id, result=result)
    except Exception as exc:  # the worker must survive any handler failure
        retry = None if job.attempts >= job.max_attempts else backoff_seconds(job.attempts)
        queue.fail(conn, job.id, error=repr(exc), retry_in_seconds=retry)
    finally:
        if stop_heartbeat is not None:
            stop_heartbeat()
    return job
