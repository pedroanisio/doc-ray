#!/usr/bin/env python3
"""Operator CLI: enqueue the corpus-wide Prism vision backfill.

Maps the whole corpus to vision by enqueuing visual jobs through the normal queue
(bounded, resumable). Tier-1 is CPU-only and safe to run corpus-wide now; Tier-2
needs the vision model served on the GPU worker.

Usage (on the box with DATABASE_URL set to the managed cluster):
    # Tier-1 (deterministic features) across every PDF missing them:
    python scripts/enqueue_visual_backfill.py --tier extract
    # Tier-2 (embeddings) once the GPU encoder is up:
    python scripts/enqueue_visual_backfill.py --tier embed
    # Both, and re-enqueue even where present:
    python scripts/enqueue_visual_backfill.py --tier all --all

Prints the number of jobs enqueued per tier. Idempotent by default (``--only-
missing`` is on): a re-run only fills gaps.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg
from psycopg.rows import dict_row

from doc_ray.application.visual_backfill import (
    enqueue_visual_embed_jobs,
    enqueue_visual_extract_jobs,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enqueue the corpus-wide Prism vision backfill.")
    parser.add_argument("--tier", choices=("extract", "embed", "all"), default="extract")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Re-enqueue even documents that already have the tier's rows (default: only missing).",
    )
    parser.add_argument("--priority", type=int, default=100)
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("fatal: DATABASE_URL is not set", file=sys.stderr)
        return 2
    only_missing = not args.all

    with psycopg.connect(database_url, row_factory=dict_row, autocommit=True) as conn:
        if args.tier in ("extract", "all"):
            ids = enqueue_visual_extract_jobs(
                conn, only_missing=only_missing, priority=args.priority
            )
            print(f"visual_extract: enqueued {len(ids)} job(s)")
        if args.tier in ("embed", "all"):
            ids = enqueue_visual_embed_jobs(conn, only_missing=only_missing, priority=args.priority)
            print(f"visual_embed: enqueued {len(ids)} job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
