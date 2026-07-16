#!/usr/bin/env python3
"""Operator CLI: list every extracted image (page render / crop) for one document.

Reads the Prism visual-layer metadata straight from Postgres — one row per
detected image or rendered region in ``document_image`` — left-joined to its
deterministic Tier-1 features (``image_tier1_features``) and any committed
Tier-3 caption (``visual_caption``). The image *bytes* are not in Postgres:
each row carries an ``object_key`` pointing at the MinIO/S3 bucket, so this
tool prints those keys (and, with ``--s3-url``, the full ``s3://`` URI) rather
than the pixels.

Usage (on the box with DATABASE_URL set to the target cluster):
    # Default fixture document:
    python scripts/list_document_images.py
    # A specific document:
    python scripts/list_document_images.py <document_uuid>
    # As JSON (one object per image), with s3:// URIs resolved:
    python scripts/list_document_images.py --json --s3-url

Notes:
    * ``document_image`` is RLS-scoped. A DATABASE_URL that connects as the
      table owner (the local ``docray`` default) sees every row; a client-role
      connection only sees documents its principal may read.
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

# Mirrors application/persistence_visual_read.list_document_images: every crop's
# metadata + its Tier-1 features + the committed caption (if any), in stable page
# order. Kept as one query so a single connection round-trip lists the document.
_QUERY = """
    SELECT di.id, di.page_number, di.source, di.xref,
           di.width_px, di.height_px, di.object_key, di.sha256,
           di.detector, di.detector_version, di.created_at,
           f.image_class, f.aspect_ratio, f.mean_saturation, f.mean_value,
           f.contrast, f.edge_density,
           vc.caption, vc.grounding_score
    FROM document_image di
    LEFT JOIN image_tier1_features f ON f.document_image_id = di.id
    LEFT JOIN visual_caption vc
           ON vc.document_image_id = di.id AND vc.status = 'committed'
    WHERE di.document_id = %(doc)s::uuid
    ORDER BY di.page_number, di.id
    LIMIT %(limit)s
"""


def fetch_images(
    conn: psycopg.Connection[Any], document_id: str, *, limit: int
) -> list[dict[str, Any]]:
    """Return the document's image rows (metadata + Tier-1 features + caption)."""
    cur = conn.execute(_QUERY, {"doc": document_id, "limit": limit})
    return cur.fetchall()


def _s3_uri(object_key: str) -> str:
    """Prefix an object key with ``s3://<bucket>/`` from the S3_BUCKET env."""
    bucket = os.environ.get("S3_BUCKET", "documents")
    return f"s3://{bucket}/{object_key}"


def _print_table(rows: list[dict[str, Any]], *, show_s3_url: bool) -> None:
    """Print one line per image, page-ordered, with the key or full s3:// URI."""
    for row in rows:
        location = _s3_uri(row["object_key"]) if show_s3_url else row["object_key"]
        image_class = row["image_class"] or "-"
        caption = row["caption"]
        print(
            f"p{row['page_number']:<4} "
            f"{row['source']:<16} "
            f"{row['width_px']:>5}x{row['height_px']:<5} "
            f"{image_class:<11} "
            f"{location}"
        )
        if caption:
            score = row["grounding_score"]
            score_text = f" (grounding={score:.2f})" if score is not None else ""
            print(f"        caption:{score_text} {caption}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List every extracted image (page render / crop) for one document."
    )
    parser.add_argument(
        "document_id",
        nargs="?",
        default=DEFAULT_DOCUMENT_ID,
        help=f"Document UUID (default: {DEFAULT_DOCUMENT_ID}).",
    )
    parser.add_argument(
        "--limit", type=int, default=1000, help="Max images to list (default 1000)."
    )
    parser.add_argument(
        "--s3-url",
        action="store_true",
        help="Show the full s3://<bucket>/<key> URI instead of the bare object key.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text table.")
    args = parser.parse_args(argv)

    loaded_from = load_env_fallback(("DATABASE_URL", "S3_BUCKET"))
    if loaded_from:
        print(f"(loaded DATABASE_URL/S3_BUCKET from {loaded_from})", file=sys.stderr)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "fatal: DATABASE_URL is not set (and no .env/.env.prod supplied it).\n"
            "Set it explicitly, e.g.:\n"
            "  DATABASE_URL=\"$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)\" "
            "python3 scripts/list_document_images.py",
            file=sys.stderr,
        )
        return 2

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        try:
            rows = fetch_images(conn, args.document_id, limit=args.limit)
        except psycopg.errors.UndefinedTable:
            print(
                "fatal: the Prism visual-layer tables (document_image, ...) do not "
                "exist on this database.\n"
                "Apply migrations 089/091/093 before extracted images can be listed.",
                file=sys.stderr,
            )
            return 3

    if args.json:
        payload = []
        for row in rows:
            item = dict(row)
            item["id"] = str(item["id"])
            item["created_at"] = item["created_at"].isoformat()
            if args.s3_url:
                item["s3_uri"] = _s3_uri(item["object_key"])
            payload.append(item)
        print(json.dumps(payload, indent=2, default=str))
        return 0

    if not rows:
        print(f"No images found for document {args.document_id}.")
        print(
            "(Either the document has no extracted images yet, or the "
            "connection's RLS scope excludes it.)"
        )
        return 0

    print(f"document {args.document_id} — {len(rows)} image(s):\n")
    _print_table(rows, show_s3_url=args.s3_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
