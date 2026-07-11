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
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import psycopg

from doc_ray.adapters import queue_postgres as queue
from doc_ray.domain.jobs import (
    KIND_ANNOTATE,
    KIND_BACKUP,
    KIND_CANONICALIZE,
    KIND_DELETE,
    KIND_INGEST,
    KIND_REANNOTATE,
    KIND_RESTORE,
    KIND_SEED_APPROVED,
    KIND_SEED_CANONICAL,
    KIND_SEED_SUGGEST,
    KIND_SEMANTIC_ENRICH,
    KIND_UPDATE_METADATA,
    Job,
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


def backoff_seconds(attempts: int) -> float:
    """Return the exponential backoff (capped) for the *attempts*-th failure."""
    return float(min(_BACKOFF_BASE_SECONDS * 2 ** max(0, attempts - 1), _BACKOFF_CAP_SECONDS))


def handle_delete(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Delete a document (cascading to all its derived layers) and its blob.

    ``source_uri`` is read before the row is gone (the DELETE cascades
    immediately), then the object-store blob is removed too — otherwise every
    delete permanently leaks the original bytes (BL-097). The DB delete is
    the authoritative operation; a document with no configured store, or
    whose source_uri doesn't belong to this store's bucket (unexpected, but
    not this handler's problem to fix), still gets its catalog row removed —
    blob cleanup is a bonus, not a precondition for deletion to succeed.
    """
    row = conn.execute(
        "SELECT source_uri FROM documents WHERE id = %s", (job.document_id,)
    ).fetchone()
    conn.execute("DELETE FROM documents WHERE id = %s", (job.document_id,))
    if store is not None and row and row["source_uri"]:
        prefix = f"s3://{store.bucket}/"
        source_uri = row["source_uri"]
        if source_uri.startswith(prefix):
            store.delete(source_uri[len(prefix) :])
    return {"deleted": job.document_id}


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


def handle_backup(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Create a full backup archive (DB dump + object-store blobs)."""
    from doc_ray.application import backup as bk

    if store is None:
        raise RuntimeError("backup requires an object store")
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
    return bk.restore_backup(conn, store, str(backup_id))


HANDLERS: dict[str, Handler] = {
    KIND_ANNOTATE: handle_annotate,
    KIND_SEMANTIC_ENRICH: handle_semantic_enrich,
    KIND_REANNOTATE: handle_reannotate,
    KIND_DELETE: handle_delete,
    KIND_INGEST: handle_ingest,
    KIND_SEED_CANONICAL: handle_seed_canonical,
    KIND_SEED_SUGGEST: handle_seed_suggest,
    KIND_SEED_APPROVED: handle_seed_approved,
    KIND_CANONICALIZE: handle_canonicalize,
    KIND_BACKUP: handle_backup,
    KIND_RESTORE: handle_restore,
    KIND_UPDATE_METADATA: handle_update_metadata,
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
        # One transaction: success recording and chain advancement commit (or
        # roll back) together. A crash or enqueue failure here leaves the job
        # 'running', so the reaper requeues it and the step re-runs — no silent
        # pipeline stall, and no recorded-success-without-successor window.
        with conn.transaction():
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
    except Exception as exc:  # the worker must survive any handler failure
        retry = None if job.attempts >= job.max_attempts else backoff_seconds(job.attempts)
        # BL-115: repr(exc) in the DB column is easy to miss (an operator has to
        # think to query it); the full traceback + job context goes to the log
        # too, so a real crash during ingest leaves a durable, discoverable trace.
        _logger.error(
            "job %s (kind=%s, document_id=%s) failed on attempt %d/%d: %s",
            job.id,
            job.kind,
            job.document_id,
            job.attempts,
            job.max_attempts,
            exc,
            exc_info=exc,
        )
        queue.fail(conn, job.id, error=repr(exc), retry_in_seconds=retry)
    finally:
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
    queue.enqueue(conn, chain[0], {"chain": chain[1:]}, document_id=str(document_id))
