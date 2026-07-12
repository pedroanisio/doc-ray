"""Job dispatch: claim a job and route it to a handler (D-005).

The worker is a synchronous process; this module holds the kind→handler dispatch
table and the single claim→run→finalize step (:func:`run_once`). Handlers lazily
import the blocking machinery (spaCy for reannotation, the ingest pipeline) so this
module stays importable — and :func:`run_once` stays unit-testable with a stub
registry — without those heavy dependencies present.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, cast

import psycopg

from doc_ray.adapters import queue_postgres as queue
from doc_ray.correlation import request_id_from_payload
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
    KIND_SEED_CANONICAL,
    KIND_SEED_SUGGEST,
    KIND_SEMANTIC_ENRICH,
    KIND_SUGGEST_DOCUMENT_METADATA,
    KIND_UPDATE_METADATA,
    Job,
)

# The reporter lives in domain.progress (BL-123) so adapters can report
# phase boundaries without importing this module; every handler (and the
# worker tests) still addresses it as ``jobs.report_progress``.
from doc_ray.domain.progress import (
    report_progress,
    reset_progress_reporter,
    set_progress_reporter,
)
from doc_ray.ports.object_store import ObjectStore

_logger = logging.getLogger("doc_ray.jobs")

if TYPE_CHECKING:
    # Concrete adapter type for the (path-based) pipeline, imported for typing only
    # so this module stays importable without boto3.
    from doc_ray.adapters.object_store import ObjectStore as S3ObjectStore

#: A handler runs one job and returns a JSON-able result recorded on the job row.
Handler = Callable[[psycopg.Connection[Any], ObjectStore | None, Job], dict[str, Any]]

_BACKOFF_BASE_SECONDS = 5.0
_BACKOFF_CAP_SECONDS = 300.0
_DEFAULT_JOB_TIMEOUT_SECONDS = 3600.0


class JobExecutionTimeout(TimeoutError):
    """Raised when a running handler exceeds its configured wall-clock budget."""


def backoff_seconds(attempts: int) -> float:
    """Return the exponential backoff (capped) for the *attempts*-th failure."""
    return float(min(_BACKOFF_BASE_SECONDS * 2 ** max(0, attempts - 1), _BACKOFF_CAP_SECONDS))


def _timeout_value(raw: float | str | None) -> float | None:
    if raw is None:
        return None
    seconds = float(raw)
    return seconds if seconds > 0 else None


def execution_timeout_seconds(
    kind: str, job_timeouts: Mapping[str, float | None] | None = None
) -> float | None:
    """Return the configured execution timeout for *kind*.

    ``WORKER_JOB_TIMEOUT_SECONDS`` sets the default. A kind-specific
    ``WORKER_JOB_TIMEOUT_<KIND>_SECONDS`` override wins, where non-alphanumeric
    characters in ``kind`` become underscores. Values <= 0 disable the timeout.
    """
    if job_timeouts is not None and kind in job_timeouts:
        return _timeout_value(job_timeouts[kind])
    env_kind = "".join(ch if ch.isalnum() else "_" for ch in kind).upper()
    per_kind = os.environ.get(f"WORKER_JOB_TIMEOUT_{env_kind}_SECONDS")
    if per_kind is not None:
        return _timeout_value(per_kind)
    default = os.environ.get("WORKER_JOB_TIMEOUT_SECONDS", _DEFAULT_JOB_TIMEOUT_SECONDS)
    return _timeout_value(default)


def _job_request_id(job: Job) -> str:
    return request_id_from_payload(job.payload) or "-"


def _retry_label(retry_in_seconds: float | None) -> str:
    if retry_in_seconds is None:
        return "terminal failure"
    return f"retry in {retry_in_seconds:g}s"


@contextlib.contextmanager
def _enforce_execution_timeout(job: Job, seconds: float | None) -> Iterator[None]:
    """Raise :class:`JobExecutionTimeout` if *job* exceeds *seconds*.

    The production worker calls ``run_once`` on the main thread, where POSIX
    interval timers can interrupt blocking Python code such as ``time.sleep`` or
    network reads. Test helper threads and non-POSIX platforms fall back to no
    alarm rather than installing process-global signal state from the wrong
    thread.
    """
    if (
        seconds is None
        or seconds <= 0
        or threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "ITIMER_REAL")
    ):
        yield
        return

    def _raise_timeout(_signum: int, _frame: FrameType | None) -> None:
        raise JobExecutionTimeout(
            f"job {job.id} ({job.kind}) exceeded {seconds:g}s execution timeout"
        )

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def handle_delete(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Soft-delete a document so it can be restored individually (BL-059)."""
    from doc_ray.adapters import persistence as db

    if job.document_id is None:
        raise RuntimeError("delete requires a document_id")
    db.set_document_lifecycle(
        cast(db.DictConnection, conn),
        job.document_id,
        "deleted",
        actor=job.payload.get("actor"),
    )
    return {"deleted": job.document_id, "soft": True}


def handle_archive_document(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Archive a document without changing its ingestion status."""
    from doc_ray.adapters import persistence as db

    if job.document_id is None:
        raise RuntimeError("archive_document requires a document_id")
    changed = db.set_document_lifecycle(
        cast(db.DictConnection, conn),
        job.document_id,
        "archived",
        actor=job.payload.get("actor"),
    )
    return {"archived": job.document_id, "changed": changed}


def handle_restore_document(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Restore an archived or soft-deleted document to active lifecycle."""
    from doc_ray.adapters import persistence as db

    if job.document_id is None:
        raise RuntimeError("restore_document requires a document_id")
    db.set_document_lifecycle(
        cast(db.DictConnection, conn),
        job.document_id,
        "active",
        actor=job.payload.get("actor"),
    )
    return {"restored": job.document_id}


def handle_update_metadata(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Edit a document's title/description/tags, recording change history."""
    from doc_ray.adapters import persistence as db

    if job.document_id is None:
        raise RuntimeError("update_metadata requires a document_id")
    changed = db.update_document_metadata(
        cast(db.DictConnection, conn),
        job.document_id,
        title=job.payload.get("title"),
        description=job.payload.get("description"),
        tags=job.payload.get("tags"),
        actor=job.payload.get("actor"),
    )
    return {"changed": changed}


def handle_add_document_tags(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Merge tags into a document and record the BL-058 tags history row."""
    from doc_ray.adapters import persistence as db

    if job.document_id is None:
        raise RuntimeError("add_document_tags requires a document_id")
    changed = db.add_document_tags(
        cast(db.DictConnection, conn),
        job.document_id,
        [str(tag) for tag in job.payload.get("tags") or []],
        actor=job.payload.get("actor"),
    )
    return {"changed": changed}


def handle_reannotate(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Re-run linguistic annotation for a document (loads spaCy lazily)."""
    from doc_ray.application import reannotate as reann

    if job.document_id is None:
        raise RuntimeError("reannotate requires a document_id")
    lang = job.payload.get("lang", "en")
    report_progress(0, 1, "Re-annotating document")
    info = reann.reannotate_document(conn, job.document_id, cache={}, lang_default=lang)
    return info or {"skipped": True}


def handle_reextract(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Backfill page/bbox geometry by re-reading a document's original blob."""
    from doc_ray.application import reextract

    if job.document_id is None:
        raise RuntimeError("reextract requires a document_id")
    if store is None:
        raise RuntimeError("reextract requires an object store")
    report_progress(0, 1, "Re-extracting document geometry")
    return reextract.backfill_document_geometry_from_store(conn, store, job.document_id)


def handle_ingest(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Ingest an uploaded blob: fetch bytes, run the path-based pipeline.

    The blob is materialised under the payload's ``original_filename`` (basename
    only — never a caller-controlled path) so the pipeline derives the document's
    title/filename from the *uploaded* name, not a random temp name. An explicit
    payload ``title`` overrides whatever the extractor derived.
    """
    from doc_ray.application.integrity import verify_bytes
    from doc_ray.application.pipeline import ingest_file

    if store is None:
        raise RuntimeError("ingest requires an object store")
    report_progress(0, 3, "Reading upload")
    storage_key = str(job.payload["storage_key"])
    data = verify_bytes(
        store.get(storage_key),
        job.payload.get("sha256"),
        context=f"ingest upload {storage_key}",
    )
    report_progress(1, 3, "Preparing uploaded file")
    original = job.payload.get("original_filename") or f"upload{job.payload.get('extension', '')}"
    safe_name = Path(str(original)).name or "upload"
    # pipeline.ingest_file is path-based; materialise the blob under its real name so
    # the proven pipeline is reused unchanged (its re-PUT is content-addressed/idempotent).
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / safe_name
        tmp_path.write_bytes(data)
        # store is the concrete S3 adapter at runtime; the pipeline is typed against it.
        report_progress(2, 3, "Extracting and storing document")
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
    report_progress(0, 1, "Annotating document")
    info = SpacyAnnotator().annotate(
        conn, job.document_id, lang_default=str(job.payload.get("lang", "en"))
    )
    # Record the detected language on the document: the migration-016 trigger
    # then rebuilds the content tsvector with the language-aware config, so a
    # separate reannotate pass is no longer needed for stemmed search.
    lang = info.get("lang")
    if lang:
        conn.execute(
            "UPDATE documents SET language = %s WHERE id = %s AND language IS DISTINCT FROM %s",
            (lang, job.document_id, lang),
        )
    return info


def handle_semantic_enrich(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Run deterministic discourse/time/sentiment/embedding enrichment."""
    from doc_ray.application import semantic_enrichment

    if job.document_id is None:
        raise RuntimeError("semantic_enrich requires a document_id")
    report_progress(0, 1, "Enriching semantic layers")
    return semantic_enrichment.annotate_document(conn, job.document_id)


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


def handle_seed_suggest(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Mine and KB-resolve a document's seed candidates into ``seed_suggestion``.

    Nothing is seeded here — rows land as ``pending`` for human curation (the
    webapp's audit screen). Payload knobs: ``limit``, ``min_mentions``,
    ``min_lexemes``.
    """
    from doc_ray.adapters.wikidata import WikidataClient
    from doc_ray.adapters.wikipedia import WikipediaClient
    from doc_ray.application import seed_suggest

    if job.document_id is None:
        raise RuntimeError("seed_suggest requires a document_id")
    report_progress(0, 1, "Mining seed candidates")
    wiki = WikipediaClient()
    kb = WikidataClient()
    try:
        return seed_suggest.suggest_for_document(
            conn,
            wiki,
            kb,
            job.document_id,
            limit=int(job.payload.get("limit", 40)),
            min_mentions=int(job.payload.get("min_mentions", 3)),
            min_lexemes=int(job.payload.get("min_lexemes", 10)),
        )
    finally:
        wiki.close()
        kb.close()


def handle_seed_approved(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Seed this document's curator-approved suggestions into the registry.

    Reads the approved+resolved QIDs, seeds them through the ordinary
    ``canonical_seed`` path, and advances those rows to ``seeded`` (final).
    """
    from doc_ray.adapters import seed_suggestion_store as sstore
    from doc_ray.adapters.wikidata import WikidataClient
    from doc_ray.application import canonical_seed

    if job.document_id is None:
        raise RuntimeError("seed_approved requires a document_id")
    report_progress(0, 1, "Seeding approved entities")
    qids = sstore.approved_qids(conn, job.document_id)
    if not qids:
        return {"seeded": 0}
    kb = WikidataClient()
    try:
        ids = canonical_seed.seed_from_ids(conn, kb, qids)
    finally:
        kb.close()
    sstore.mark_seeded(conn, job.document_id)
    return {"seeded": len(ids)}


def handle_suggest_document_metadata(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Generate verified document metadata suggestions for human review."""
    from doc_ray.application import document_metadata_suggest

    if job.document_id is None:
        raise RuntimeError("suggest_document_metadata requires a document_id")
    report_progress(0, 1, "Generating metadata suggestions")
    return document_metadata_suggest.generate_suggestions(
        cast(psycopg.Connection[dict[str, Any]], conn), job.document_id
    )


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
    report_progress(0, 2, "Scanning registry terms")
    terms = term_scan.scan_document(conn, job.document_id)
    report_progress(1, 2, "Linking entity mentions")
    result = canonicalize.autolink_document(conn, job.document_id)
    out: dict[str, Any] = {
        "term_mentions": terms,
        "linked": result.linked,
        "residue": len(result.residue),
    }
    if job.payload.get("resolve") and result.residue:
        report_progress(2, 3, "Resolving remaining mentions")
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


def handle_backup(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Create a full backup archive (DB dump + object-store blobs)."""
    from doc_ray.application import backup as bk

    if store is None:
        raise RuntimeError("backup requires an object store")
    report_progress(0, 1, "Creating backup archive")
    summary = bk.create_backup(conn, store, note=job.payload.get("note"))
    # Keep the recorded result compact; the per-table breakdown lives in the archive.
    return {
        k: summary[k]
        for k in ("id", "object_key", "archive_bytes", "table_rows", "blob_count", "doc_count")
    }


def handle_restore(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Restore the corpus (DB data + blobs) from a catalogued backup archive."""
    from doc_ray.application import backup as bk

    if store is None:
        raise RuntimeError("restore requires an object store")
    backup_id = job.payload.get("backup_id")
    if not backup_id:
        raise RuntimeError("restore requires a backup_id")
    report_progress(0, 1, "Restoring backup archive")
    return bk.restore_backup(conn, store, str(backup_id))


HANDLERS: dict[str, Handler] = {
    KIND_ANNOTATE: handle_annotate,
    KIND_SEMANTIC_ENRICH: handle_semantic_enrich,
    KIND_REANNOTATE: handle_reannotate,
    KIND_REEXTRACT: handle_reextract,
    KIND_DELETE: handle_delete,
    KIND_ARCHIVE_DOCUMENT: handle_archive_document,
    KIND_RESTORE_DOCUMENT: handle_restore_document,
    KIND_INGEST: handle_ingest,
    KIND_SEED_CANONICAL: handle_seed_canonical,
    KIND_SEED_SUGGEST: handle_seed_suggest,
    KIND_SEED_APPROVED: handle_seed_approved,
    KIND_SUGGEST_DOCUMENT_METADATA: handle_suggest_document_metadata,
    KIND_CANONICALIZE: handle_canonicalize,
    KIND_BACKUP: handle_backup,
    KIND_RESTORE: handle_restore,
    KIND_UPDATE_METADATA: handle_update_metadata,
    KIND_ADD_DOCUMENT_TAGS: handle_add_document_tags,
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
    job_timeouts: Mapping[str, float | None] | None = None,
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
        job_timeouts: Optional per-kind timeout overrides; values <= 0 disable.

    Returns:
        The claimed :class:`Job`, or ``None`` if the queue was empty.
    """
    registry = handlers if handlers is not None else HANDLERS
    job = queue.claim(conn, worker_id=worker_id)
    if job is None:
        _logger.debug("worker %s found no queued job", worker_id)
        return None
    request_id = _job_request_id(job)
    _logger.info(
        "worker %s claimed job %s (kind=%s, document_id=%s, attempt=%d/%d, request_id=%s)",
        worker_id,
        job.id,
        job.kind,
        job.document_id,
        job.attempts,
        job.max_attempts,
        request_id,
    )
    handler = registry.get(job.kind)
    if handler is None:
        _logger.error(
            "worker %s rejected job %s (unknown kind %s, document_id=%s, "
            "attempt=%d/%d, request_id=%s)",
            worker_id,
            job.id,
            job.kind,
            job.document_id,
            job.attempts,
            job.max_attempts,
            request_id,
        )
        queue.fail(conn, job.id, error=f"unknown job kind {job.kind!r}", retry_in_seconds=None)
        return job
    stop_heartbeat = (
        _start_heartbeat(heartbeat_conn, job, worker_id, heartbeat_interval)
        if heartbeat_conn is not None
        else None
    )
    last_progress: tuple[int | None, int | None, str | None] = (None, None, None)

    def _report(current: int | None, total: int | None, stage: str | None) -> None:
        nonlocal last_progress
        last_progress = (current, total, stage)
        queue.update_progress(
            conn,
            job.id,
            current=current,
            total=total,
            stage=stage,
            worker_id=worker_id,
        )

    token = set_progress_reporter(_report)
    try:
        report_progress(0, None, f"Starting {job.kind}")
        with _enforce_execution_timeout(job, execution_timeout_seconds(job.kind, job_timeouts)):
            result = handler(conn, store, job)
        # One transaction: success recording and chain advancement commit (or
        # roll back) together. A crash or enqueue failure here leaves the job
        # 'running', so the reaper requeues it and the step re-runs — no silent
        # pipeline stall, and no recorded-success-without-successor window.
        with conn.transaction():
            if queue.is_canceled(conn, job.id):
                _logger.info(
                    "worker %s job %s (kind=%s, request_id=%s) was canceled before completion",
                    worker_id,
                    job.id,
                    job.kind,
                    request_id,
                )
                return job
            _, total, _ = last_progress
            if total is None:
                total = last_progress[0] or 1
            report_progress(total, total, "Completed")
            if queue.complete(conn, job.id, result=result, worker_id=worker_id):
                if (
                    job.document_id is None
                    and isinstance(result, dict)
                    and result.get("document_id")
                ):
                    # Backfill the row (ingest starts with no document): progress
                    # surfaces filter on jobs.document_id and must include step 1.
                    conn.execute(
                        "UPDATE jobs SET document_id = %s WHERE id = %s",
                        (str(result["document_id"]), job.id),
                    )
                _advance_chain(conn, job, result)
                _logger.info(
                    "worker %s completed job %s (kind=%s, document_id=%s, request_id=%s)",
                    worker_id,
                    job.id,
                    job.kind,
                    job.document_id
                    or (str(result["document_id"]) if result.get("document_id") else None),
                    request_id,
                )
            else:
                _logger.warning(
                    "worker %s could not complete job %s (kind=%s, document_id=%s, "
                    "request_id=%s); claim may have been lost",
                    worker_id,
                    job.id,
                    job.kind,
                    job.document_id,
                    request_id,
                )
    except Exception as exc:  # the worker must survive any handler failure
        retry = None if job.attempts >= job.max_attempts else backoff_seconds(job.attempts)
        # BL-115: repr(exc) in the DB column is easy to miss (an operator has to
        # think to query it); the full traceback + job context goes to the log
        # too, so a real crash during ingest leaves a durable, discoverable trace.
        _logger.error(
            "job %s (kind=%s, document_id=%s, worker=%s, request_id=%s) "
            "failed on attempt %d/%d (%s): %s",
            job.id,
            job.kind,
            job.document_id,
            worker_id,
            request_id,
            job.attempts,
            job.max_attempts,
            _retry_label(retry),
            exc,
            exc_info=exc,
        )
        queue.fail(conn, job.id, error=repr(exc), retry_in_seconds=retry)
    finally:
        reset_progress_reporter(token)
        if stop_heartbeat is not None:
            stop_heartbeat()
    return job


def _advance_chain(conn: psycopg.Connection[Any], job: Job, result: dict[str, Any]) -> None:
    """Enqueue the next pipeline step from the payload's ``chain``, if any.

    The chain travels in the payload (``["annotate", "canonicalize"]``): each
    successful step enqueues the next kind for the same document with the
    remaining chain — deterministic and auditable in the jobs table. An ingest
    step starts with no ``document_id``: the chained step picks it up from the
    handler's result, and the ingest job's own row is backfilled so progress
    surfaces (``jobs(documentId:)``, the webapp cascade) include it. A failed
    step never advances (the retry path re-runs the same step, so the chain
    survives transient failures), and an ingest that did not actually ingest
    (extraction failure, duplicate re-upload) ends the chain rather than
    re-processing.
    """
    chain = list(job.payload.get("chain") or [])
    if not chain:
        return
    if job.kind == KIND_INGEST and result.get("status") != "ingested":
        return
    document_id = job.document_id or (
        result.get("document_id") if isinstance(result, dict) else None
    )
    if document_id is None:
        return
    payload: dict[str, Any] = {"chain": chain[1:]}
    if isinstance(job.payload.get("_meta"), dict):
        payload["_meta"] = dict(job.payload["_meta"])
    queue.enqueue(conn, chain[0], payload, document_id=str(document_id))
