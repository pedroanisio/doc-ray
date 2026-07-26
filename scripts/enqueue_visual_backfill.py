#!/usr/bin/env python3
"""Operator CLI: enqueue the corpus-wide Prism vision backfill.

Maps the whole corpus to vision by enqueuing visual jobs through the normal queue
(bounded, resumable). Tier-1 is CPU-only and safe to run corpus-wide now; Tier-2
needs the vision encoder on the GPU worker; Tier-3 additionally needs the VLM
served (Ollama) and costs one model round trip per image — run it last.

Usage (on the box with DATABASE_URL set to the managed cluster):
    # Tier-1 (deterministic features) across every PDF missing them:
    python scripts/enqueue_visual_backfill.py --tier extract
    # Tier-2 (embeddings) once the GPU encoder is up:
    python scripts/enqueue_visual_backfill.py --tier embed
    # Tier-3 (grounded VLM captions) once the VLM is served:
    python scripts/enqueue_visual_backfill.py --tier caption
    # All three, and re-enqueue even where present:
    python scripts/enqueue_visual_backfill.py --tier all --all

``--tier all`` covers extract + embed only. Tier-3 is opt-in by name: it is the
most expensive work in the system and must not be swept up by a habit-typed
"all".

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
    enqueue_visual_caption_jobs,
    enqueue_visual_embed_jobs,
    enqueue_visual_extract_jobs,
)

#: The trusted writer the worker itself runs as (migration 106). Operator tooling
#: assumes it so a corpus-wide sweep sees the corpus; the application module it
#: calls stays RLS-honest for API callers.
_BATCH_ROLE = "docray_batch"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enqueue the corpus-wide Prism vision backfill.")
    parser.add_argument("--tier", choices=("extract", "embed", "caption", "all"), default="extract")
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
        # Corpus-wide operator work: assume the trusted batch role for the whole
        # run. On the managed cluster DATABASE_URL logs in as `docray`, which is
        # bound by the `documents` read policy and carries no principal in
        # app.principal_id — so every selection below would return zero rows and
        # this tool would report "enqueued 0 job(s)" against a full corpus.
        # Authority here comes from holding the database credential, exactly as
        # it does for the worker that runs under the same role.
        conn.execute(f"SET ROLE {_BATCH_ROLE}")
        if args.tier in ("extract", "all"):
            ids = enqueue_visual_extract_jobs(
                conn, only_missing=only_missing, priority=args.priority
            )
            print(f"visual_extract: enqueued {len(ids)} job(s)")
        if args.tier in ("embed", "all"):
            ids = enqueue_visual_embed_jobs(conn, only_missing=only_missing, priority=args.priority)
            print(f"visual_embed: enqueued {len(ids)} job(s)")
        # Tier-3 only on an explicit --tier caption; see the module docstring.
        if args.tier == "caption":
            ids = enqueue_visual_caption_jobs(
                conn, only_missing=only_missing, priority=args.priority
            )
            print(f"visual_caption: enqueued {len(ids)} job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
