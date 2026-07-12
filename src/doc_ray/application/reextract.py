"""Re-extract an already-ingested document and rebuild its derived layers.

Re-runs the current extractor over an already-ingested document and rebuilds its
derived layers (content, sentences, spans, lexeme annotations).

The ingest pipeline dedupes by content hash and never updates an existing
document, so this is the surgical path to apply an improved extractor to a
document already in the store. Bytes come from --file (or re-download the blob
yourself); the file's sha256 must match the stored document.

    python -m ingest.reextract --title "F-GZCP VA" --file static/fixtures/AirFrance447_BEA.pdf
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any, cast

import psycopg

from doc_ray.adapters import extraction as extract_mod
from doc_ray.adapters import persistence as db
from doc_ray.adapters import queue_postgres as queue
from doc_ray.application import reannotate
from doc_ray.application.integrity import verify_bytes
from doc_ray.config import Config
from doc_ray.domain import sentences as sent_mod
from doc_ray.domain.jobs import KIND_REEXTRACT
from doc_ray.ports.object_store import ObjectStore


def _sha(b: bytes) -> str:
    """Return the hex SHA-256 digest of *b*."""
    return hashlib.sha256(b).hexdigest()


def object_key_from_source_uri(source_uri: str, bucket: str) -> str:
    """Return the object key for a source URI that belongs to *bucket*."""
    prefix = f"s3://{bucket}/"
    if not source_uri.startswith(prefix):
        raise ValueError(f"source URI does not belong to bucket {bucket!r}: {source_uri!r}")
    key = source_uri[len(prefix) :]
    if not key:
        raise ValueError("source URI has no object key")
    return key


def backfill_document_geometry(
    conn: psycopg.Connection[Any],
    doc_id: str,
    data: bytes,
    filename: str,
    ext: str = ".pdf",
) -> dict[str, Any]:
    """Populate layout-run and span geometry for an existing document.

    Unlike :func:`reextract_document`, this is intentionally geometry-only: it
    re-runs extraction to recover page layout, verifies the extracted text still
    matches the stored canonical text, then projects geometry onto existing
    spans keyed by their character offsets. It does not delete annotations.
    """
    extracted = extract_mod.extract(data, filename=filename, extension=ext)
    full_text = extracted.full_text or ""
    row = conn.execute(
        "SELECT full_text FROM document_content WHERE document_id=%s", (doc_id,)
    ).fetchone()
    stored = (row or {}).get("full_text") or ""
    if full_text != stored:
        raise RuntimeError("re-extraction text drift: stored text differs from extracted text")

    layout_rows = db.layout_runs_from_page_runs(full_text, extracted.page_runs)
    with conn.transaction():
        if extracted.page_count is not None:
            conn.execute(
                "UPDATE documents SET page_count=%s WHERE id=%s",
                (extracted.page_count, doc_id),
            )
        db.replace_document_layout_runs(conn, doc_id, full_text, extracted.page_runs)
        spans = conn.execute(
            "SELECT char_start, char_end FROM spans WHERE document_id=%s",
            (doc_id,),
        ).fetchall()
        pairs = [(int(r["char_start"]), int(r["char_end"])) for r in spans]
        db.ensure_spans(conn, doc_id, pairs)
        positioned = conn.execute(
            "SELECT count(*) AS n FROM spans WHERE document_id=%s AND page_number IS NOT NULL",
            (doc_id,),
        ).fetchone()

    return {
        "extractor": extracted.extractor,
        "layout_runs": len(layout_rows),
        "spans_checked": len(spans),
        "spans_positioned": int((positioned or {}).get("n") or 0),
    }


def backfill_document_geometry_from_store(
    conn: psycopg.Connection[Any], store: ObjectStore, doc_id: str
) -> dict[str, Any]:
    """Fetch a document's original blob and backfill its span geometry."""
    row = conn.execute(
        """
        SELECT source_uri, content_sha256, original_filename, doc_type, title
        FROM documents WHERE id=%s
        """,
        (doc_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"document {doc_id!r} not found")
    source_uri = row.get("source_uri")
    if not source_uri:
        raise RuntimeError(f"document {doc_id!r} has no source_uri")
    key = object_key_from_source_uri(str(source_uri), store.bucket)
    data = verify_bytes(
        store.get(key),
        row.get("content_sha256"),
        context=f"document {doc_id} original",
    )

    original = str(row.get("original_filename") or row.get("title") or "document")
    ext = Path(original).suffix or f".{row.get('doc_type') or 'pdf'}"
    filename = Path(original).stem or "document"
    return backfill_document_geometry(conn, doc_id, data, filename, ext)


def enqueue_reextract_jobs(
    conn: psycopg.Connection[Any],
    *,
    only_missing_geometry: bool = True,
    priority: int = 100,
) -> list[str]:
    """Enqueue re-extraction jobs for PDF documents in the corpus."""
    missing_clause = (
        "AND NOT EXISTS ("
        " SELECT 1 FROM spans sp WHERE sp.document_id = d.id AND sp.page_number IS NOT NULL"
        ")"
        if only_missing_geometry
        else ""
    )
    rows = conn.execute(
        f"""
        SELECT d.id
        FROM documents d
        WHERE d.status = 'extracted'
          AND d.doc_type = 'pdf'
          AND d.source_uri IS NOT NULL
          {missing_clause}
        ORDER BY d.created_at, d.id
        """
    ).fetchall()
    return [
        queue.enqueue(conn, KIND_REEXTRACT, {}, priority=priority, document_id=str(row["id"]))
        for row in rows
    ]


def reextract_document(
    conn: psycopg.Connection[Any],
    doc_id: str,
    data: bytes,
    filename: str,
    ext: str = ".pdf",
) -> dict[str, Any]:
    """Re-extract *data* and rebuild content + sentences + spans for *doc_id*.

    Args:
        conn: Live database connection.
        doc_id: Id of the document to rebuild.
        data: Raw document bytes (must hash to the stored document).
        filename: Original filename (used as a title fallback).
        ext: File extension, including the leading dot.

    Returns:
        A summary dict with ``extractor``, ``chars`` and ``sentences`` counts.
    """
    extracted = extract_mod.extract(data, filename=filename, extension=ext)
    full_text = extracted.full_text or ""
    sentences = sent_mod.build_sentences(full_text)

    with conn.transaction():
        # Tear down the old derived layers (offsets are about to change).
        # Deleting the annotations cascades to their occurrences.
        conn.execute("DELETE FROM annotations WHERE document_id=%s", (doc_id,))
        conn.execute("DELETE FROM sentences WHERE document_id=%s", (doc_id,))
        conn.execute("DELETE FROM spans WHERE document_id=%s", (doc_id,))
        # Replace the canonical text and refresh document stats.
        conn.execute(
            "UPDATE document_content SET full_text=%s WHERE document_id=%s", (full_text, doc_id)
        )
        conn.execute(
            "UPDATE documents SET char_count=%s, text_sha256=%s, page_count=%s WHERE id=%s",
            (len(full_text), _sha(full_text.encode("utf-8")), extracted.page_count, doc_id),
        )
        db.replace_document_layout_runs(conn, doc_id, full_text, extracted.page_runs)
        # Rebuild sentences + their spans.
        if sentences:
            offsets = [(cast(int, s["char_start"]), cast(int, s["char_end"])) for s in sentences]
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
                            span_map[(cast(int, s["char_start"]), cast(int, s["char_end"]))],
                        )
                        for s in sentences
                    ],
                )
    return {"extractor": extracted.extractor, "chars": len(full_text), "sentences": len(sentences)}


def main(argv: list[str] | None = None) -> int:
    """Run the re-extraction CLI.

    Args:
        argv: Argument vector; defaults to ``sys.argv`` when ``None``.

    Returns:
        Process exit code (``0`` on success, ``1`` on a lookup/hash mismatch).
    """
    ap = argparse.ArgumentParser(description="Re-extract stored documents.")
    ap.add_argument("--enqueue-all", action="store_true", help="Queue PDF geometry backfill jobs.")
    ap.add_argument(
        "--include-positioned",
        action="store_true",
        help="With --enqueue-all, also queue PDFs that already have positioned spans.",
    )
    ap.add_argument("--title")
    ap.add_argument("--file", type=Path)
    args = ap.parse_args(argv)

    if args.enqueue_all:
        cfg = Config.from_env()
        with db.connect(cfg) as conn:
            jobs = enqueue_reextract_jobs(conn, only_missing_geometry=not args.include_positioned)
        print(f"queued {len(jobs)} reextract jobs", flush=True)
        return 0

    if not args.title or not args.file:
        ap.error("--title and --file are required unless --enqueue-all is used")

    data = args.file.read_bytes()
    cfg = Config.from_env()
    with db.connect(cfg) as conn:
        row = conn.execute(
            "SELECT id, content_sha256 FROM documents WHERE title=%s", (args.title,)
        ).fetchone()
        if not row:
            print(f"No document titled {args.title!r}", file=sys.stderr)
            return 1
        if _sha(data) != row["content_sha256"]:
            print("Refusing: --file sha256 does not match the stored document.", file=sys.stderr)
            return 1

        doc_id = str(row["id"])
        r = reextract_document(conn, doc_id, data, filename=args.file.stem)
        print(
            f"  re-extracted [{r['extractor']}]: {r['chars']:,} chars, "
            f"{r['sentences']:,} sentences",
            flush=True,
        )
        lang, occ = _reannotate(conn, doc_id)
        print(f"  re-annotated: lang={lang}, {occ:,} occurrences", flush=True)
    return 0


def _reannotate(conn: psycopg.Connection[Any], doc_id: str) -> tuple[str | None, int]:
    """Re-annotate *doc_id* and return its ``(language, occurrence_count)``."""
    cache: dict[Any, Any] = {}
    res = reannotate.reannotate_document(conn, doc_id, cache, "en")
    return (res["lang"], res["occ"]) if res else (None, 0)


if __name__ == "__main__":
    raise SystemExit(main())
