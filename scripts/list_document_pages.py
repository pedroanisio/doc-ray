#!/usr/bin/env python3
"""Operator CLI: list every extracted page for one document.

A PDF is ingested page by page; each processed page is recorded in
``document_pages`` — the authoritative per-page record (BL-126). One row per
page carries its geometry (``page_width`` x ``page_height``, in PDF points) and
text presence (``has_text`` / ``text_run_count``). This tool lists those rows in
page order, so an operator can see exactly which pages were extracted and which
are text-free (e.g. scanned/image-only pages that would need OCR).

The rendered page *image* is not in Postgres: it is produced on demand and cached
in the object store under ``rendered-pages/{version}/p{page}-dpi144.png``. Pass
``--render-key`` to print that derived cache key alongside each page.

Usage:
    # Default fixture document:
    python scripts/list_document_pages.py
    # A specific document, JSON output:
    python scripts/list_document_pages.py <document_uuid> --json
    # Only pages with no extracted text (OCR candidates):
    python scripts/list_document_pages.py --no-text-only

Notes:
    * ``document_pages`` is RLS-scoped. A DATABASE_URL that connects as the table
      owner sees every row; a client-role connection only sees documents its
      principal may read.
    * DATABASE_URL is read from the environment, falling back to the repo's
      ``.env``/``.env.prod`` for any unset key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import psycopg
from _env_fallback import load_env_fallback
from psycopg.rows import dict_row

# The fixture document used when no id is passed on the command line.
DEFAULT_DOCUMENT_ID = "84cb824b-efbb-41cf-bfdb-942b7eec4b65"

# The on-demand page-render cache key (entrypoints/api/downloads.py). ``version``
# is the document's content hash when known, else the document id.
_PDF_PAGE_RENDER_DPI = 144

_QUERY = """
    SELECT p.page_number, p.page_width, p.page_height, p.has_text, p.text_run_count,
           d.content_sha256, d.id::text AS document_id
    FROM document_pages p
    JOIN documents d ON d.id = p.document_id
    WHERE p.document_id = %(doc)s::uuid
      AND (%(no_text_only)s IS FALSE OR p.has_text IS FALSE)
    ORDER BY p.page_number
    LIMIT %(limit)s
"""


def fetch_pages(
    conn: psycopg.Connection[Any], document_id: str, *, limit: int, no_text_only: bool
) -> list[dict[str, Any]]:
    """Return the document's per-page rows in page order."""
    cur = conn.execute(_QUERY, {"doc": document_id, "limit": limit, "no_text_only": no_text_only})
    return cur.fetchall()


def _render_key(row: dict[str, Any]) -> str:
    """Derive the object-store cache key for this page's rendered PNG."""
    version = row["content_sha256"] or row["document_id"]
    return f"rendered-pages/{version}/p{row['page_number']}-dpi{_PDF_PAGE_RENDER_DPI}.png"


def _print_table(rows: list[dict[str, Any]], *, show_render_key: bool) -> None:
    """Print one line per page: number, dimensions, text presence, run count."""
    for row in rows:
        text = "text" if row["has_text"] else "no-text"
        line = (
            f"p{row['page_number']:<5} "
            f"{row['page_width']:>7.1f}x{row['page_height']:<7.1f} "
            f"{text:<8} "
            f"runs={row['text_run_count']:<4}"
        )
        if show_render_key:
            line += f" {_render_key(row)}"
        print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List every extracted page (geometry + text presence) for one document."
    )
    parser.add_argument(
        "document_id",
        nargs="?",
        default=DEFAULT_DOCUMENT_ID,
        help=f"Document UUID (default: {DEFAULT_DOCUMENT_ID}).",
    )
    parser.add_argument(
        "--limit", type=int, default=100_000, help="Max pages to list (default 100000)."
    )
    parser.add_argument(
        "--no-text-only",
        action="store_true",
        help="List only pages with no extracted text (OCR candidates).",
    )
    parser.add_argument(
        "--render-key",
        action="store_true",
        help="Also print each page's rendered-PNG object-store cache key.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text table.")
    args = parser.parse_args(argv)

    loaded_from = load_env_fallback(("DATABASE_URL",))
    if loaded_from:
        print(f"(loaded DATABASE_URL from {loaded_from})", file=sys.stderr)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "fatal: DATABASE_URL is not set (and no .env/.env.prod supplied it).\n"
            "Set it explicitly, e.g.:\n"
            "  DATABASE_URL=\"$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)\" "
            "python3 scripts/list_document_pages.py",
            file=sys.stderr,
        )
        return 2

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        try:
            rows = fetch_pages(
                conn, args.document_id, limit=args.limit, no_text_only=args.no_text_only
            )
        except psycopg.errors.UndefinedTable:
            print(
                "fatal: the document_pages table does not exist on this database.\n"
                "Apply migration 069_document_pages.sql first.",
                file=sys.stderr,
            )
            return 3

    if args.json:
        payload = []
        for row in rows:
            item = dict(row)
            if args.render_key:
                item["render_key"] = _render_key(row)
            payload.append(item)
        print(json.dumps(payload, indent=2, default=str))
        return 0

    if not rows:
        print(f"No pages found for document {args.document_id}.")
        print(
            "(Either the document has not been extracted yet, the connection's "
            "RLS scope excludes it, or --no-text-only matched nothing.)"
        )
        return 0

    with_text = sum(1 for r in rows if r["has_text"])
    print(
        f"document {args.document_id} — {len(rows)} page(s) "
        f"({with_text} with text, {len(rows) - with_text} without):\n"
    )
    _print_table(rows, show_render_key=args.render_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
