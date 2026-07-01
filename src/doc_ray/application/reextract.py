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
from doc_ray.application import reannotate
from doc_ray.config import Config
from doc_ray.domain import sentences as sent_mod


def _sha(b: bytes) -> str:
    """Return the hex SHA-256 digest of *b*."""
    return hashlib.sha256(b).hexdigest()


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
    ap = argparse.ArgumentParser(description="Re-extract one stored document.")
    ap.add_argument("--title", required=True)
    ap.add_argument("--file", required=True, type=Path)
    args = ap.parse_args(argv)

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
