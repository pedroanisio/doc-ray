"""Repair a document whose ingest stored the bytes but read no text.

WHY THIS EXISTS. Ingest dedupes by content hash and refuses to re-process bytes
it has already seen (``application.pipeline.ingest_bytes``), so a document that
landed in ``status='failed'`` is unreachable by re-upload: the identical file is
recognised and the same failure is reported again. Before this module the only
remedy was to alter the source file so its hash changed — which is absurd when
the failure was never about the file. A 368-page scan is perfectly good input to
an extractor that can OCR; it was the *deployment* that could not read it.

So this is the path that applies a now-capable extractor to bytes already in the
store. It is deliberately narrow:

* only a ``failed`` document is eligible — a healthy document's re-extraction is
  ``application.reextract``, which preserves annotations;
* the stored bytes are hash-verified before being handed to the extractor;
* an extraction that still reads nothing leaves the document ``failed`` and
  records *why this attempt* failed, replacing the stale error rather than
  half-promoting a document with no text;
* success promotes the document to ``extracted`` and appends (never rewrites)
  provenance, so the recovery is auditable beside the original failure.

A repaired document then owes the *whole* post-ingest pipeline — it has no
annotations, entities or embeddings, because its ingest never produced text for
them — so :func:`handle_retry_extract` starts that chain itself rather than
trusting every caller to attach one.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import psycopg

from doc_ray.adapters import extraction as extract_mod
from doc_ray.adapters import persistence as db
from doc_ray.adapters.extraction_common import Extracted
from doc_ray.application.document_stats import refresh_document_stats
from doc_ray.application.integrity import verify_bytes
from doc_ray.application.pipeline import NO_EXTRACTABLE_TEXT
from doc_ray.application.reextract import object_key_from_source_uri
from doc_ray.domain import provenance as prov_mod
from doc_ray.domain import sentences as sent_mod
from doc_ray.domain.jobs import DEFAULT_PIPELINE_CHAIN, Job
from doc_ray.domain.progress import report_progress
from doc_ray.ports.object_store import ObjectStore


def handle_retry_extract(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Re-read a failed document's stored blob and promote it if text is found.

    The counterpart to ``handle_reextract``: that one refines a document that
    already has text, this one rescues one that has none. Failing loudly is the
    point — a retry that still reads nothing must leave the document ``failed``
    with the current reason, never half-promote it.

    A repaired document owes the *whole* post-ingest pipeline: it has no
    annotations, no entities, no embeddings, because its ingest never produced
    text for them. The chain is therefore started here rather than left to the
    payload, so every caller — webapp button, GraphQL mutation, corpus sweep —
    yields a document that is actually readable, searchable, and linked.
    """
    # Late import: ``application.jobs`` imports the handler registry, which
    # imports this module.
    from doc_ray.application.jobs import enqueue_chain_head

    if job.document_id is None:
        raise RuntimeError("retry_extract requires a document_id")
    if store is None:
        raise RuntimeError("retry_extract requires an object store")
    result = retry_failed_extraction(conn, store, job.document_id)
    refresh_document_stats(conn, job.document_id)
    if not job.payload.get("chain"):
        meta = job.payload.get("_meta")
        enqueue_chain_head(
            conn,
            job.document_id,
            DEFAULT_PIPELINE_CHAIN,
            meta=meta if isinstance(meta, dict) else None,
        )
    return result


class NotRepairable(RuntimeError):
    """The document is not in a state this repair path may act on."""


def _document_row(conn: psycopg.Connection[Any], doc_id: str) -> dict[str, Any]:
    """Return the columns the repair needs, or raise if it cannot proceed."""
    row = conn.execute(
        """
        SELECT id, title, status, source_uri, content_sha256, byte_size,
               original_filename, doc_type
        FROM documents WHERE id = %s
        """,
        (doc_id,),
    ).fetchone()
    if not row:
        raise NotRepairable(f"document {doc_id!r} not found")
    status = str(row.get("status") or "")
    if status != "failed":
        raise NotRepairable(
            f"document {doc_id!r} is {status!r}, not 'failed'; "
            "use reextract for a document that already has text"
        )
    if not row.get("source_uri"):
        raise NotRepairable(f"document {doc_id!r} has no stored original to re-read")
    return dict(row)


def _record_attempt_error(conn: psycopg.Connection[Any], doc_id: str, error: str) -> None:
    """Replace the document's recorded error with this attempt's outcome.

    Committed in its own transaction so the operator sees the *current* reason a
    retry did not work, even though the job itself goes on to fail and roll its
    own work back.
    """
    with conn.transaction():
        conn.execute("UPDATE documents SET error = %s WHERE id = %s", (error, doc_id))


def retry_failed_extraction(
    conn: psycopg.Connection[Any], store: ObjectStore, doc_id: str
) -> dict[str, Any]:
    """Re-extract a failed document's stored bytes and promote it on success.

    Args:
        conn: Live database connection.
        store: Object store holding the original bytes.
        doc_id: Id of the ``failed`` document to repair.

    Returns:
        A summary dict with ``extractor``, ``chars``, ``sentences`` and
        ``page_count``.

    Raises:
        NotRepairable: The document is missing, not ``failed``, or has no blob.
        RuntimeError: Extraction ran but still produced no readable text.
    """
    row = _document_row(conn, doc_id)
    report_progress(0, 4, "Reading stored original")
    key = object_key_from_source_uri(str(row["source_uri"]), store.bucket)
    data = verify_bytes(
        store.get(key),
        row.get("content_sha256"),
        context=f"document {doc_id} original",
    )

    original = str(row.get("original_filename") or row.get("title") or "document")
    ext = Path(original).suffix or f".{row.get('doc_type') or 'pdf'}"
    stem = Path(original).stem or "document"

    report_progress(1, 4, "Re-extracting document text")
    try:
        extracted = extract_mod.extract(data, filename=stem, extension=ext)
    except Exception as exc:
        # The extractor's own diagnosis (e.g. "OCR pass failed — is Tesseract
        # installed?") is far more actionable than the stale ingest error, so it
        # replaces it before the job surfaces the failure.
        _record_attempt_error(conn, doc_id, str(exc))
        raise

    full_text = extracted.full_text or ""
    if not full_text.strip():
        _record_attempt_error(conn, doc_id, NO_EXTRACTABLE_TEXT)
        raise RuntimeError(NO_EXTRACTABLE_TEXT)

    report_progress(2, 4, "Rebuilding sentences")
    sentences = sent_mod.build_sentences(full_text)
    text_sha = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    events = prov_mod.build_events(
        content_sha256=str(row.get("content_sha256") or ""),
        source_filename=original,
        byte_size=int(row.get("byte_size") or len(data)),
        storage_bucket=store.bucket,
        storage_key=key,
        storage_uri=str(row["source_uri"]),
        status="extracted",
        extractor=extracted.extractor,
        extractor_version=extracted.extractor_version,
        page_count=extracted.page_count,
        char_count=len(full_text),
        text_sha256=text_sha,
        sentence_count=len(sentences),
        actor="retry_extract",
    )

    report_progress(3, 4, "Promoting document to extracted")
    with conn.transaction():
        _write_recovered_text(conn, doc_id, extracted, full_text, sentences, text_sha)
        db.append_provenance_events(conn, doc_id, events)
    return {
        "extractor": extracted.extractor,
        "chars": len(full_text),
        "sentences": len(sentences),
        "page_count": extracted.page_count,
    }


def _write_recovered_text(
    conn: psycopg.Connection[Any],
    doc_id: str,
    extracted: Extracted,
    full_text: str,
    sentences: list[dict[str, Any]],
    text_sha: str,
) -> None:
    """Persist recovered text, geometry and sentences, and clear the failure.

    The failed document already carries a ``document_content`` row with a NULL
    text (ingest writes one for every document, readable or not), so this
    updates in place rather than inserting.
    """
    conn.execute(
        """
        UPDATE documents
           SET status = 'extracted',
               error = NULL,
               char_count = %s,
               text_sha256 = %s,
               page_count = %s,
               mime_type = coalesce(mime_type, %s)
         WHERE id = %s
        """,
        (len(full_text), text_sha, extracted.page_count, extracted.content_type, doc_id),
    )
    conn.execute(
        "UPDATE document_content SET full_text = %s WHERE document_id = %s",
        (full_text, doc_id),
    )
    db.replace_document_pages(conn, doc_id, getattr(extracted, "page_metrics", None))
    db.replace_document_layout_runs(conn, doc_id, full_text, extracted.page_runs)
    if not sentences:
        return
    offsets = [(int(s["char_start"]), int(s["char_end"])) for s in sentences]
    span_map = db.ensure_spans(conn, doc_id, offsets)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO sentences (id, document_id, ordinal, text, span_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            [
                (
                    s["id"],
                    doc_id,
                    s["ordinal"],
                    s["text"],
                    span_map[(int(s["char_start"]), int(s["char_end"]))],
                )
                for s in sentences
            ],
        )
