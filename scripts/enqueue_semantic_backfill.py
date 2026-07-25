#!/usr/bin/env python3
"""Operator CLI: enqueue the corpus-wide learned sentence re-embed.

This is the API-independent operational path for the Layer-20 hard cutover.
Take a corpus backup first, then enqueue and let the heavy worker pool drain the
jobs. The default is idempotent after successful embedding; ``--all`` forces a
re-embed of every document that has sentences.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import psycopg
from psycopg.rows import dict_row

from doc_ray.application.semantic_embed import enqueue_semantic_embed_jobs


async def _run(database_url: str, *, only_missing: bool, priority: int) -> list[str]:
    conn = await psycopg.AsyncConnection.connect(
        database_url,
        row_factory=dict_row,
        autocommit=True,
    )
    try:
        return await enqueue_semantic_embed_jobs(
            conn,
            only_missing=only_missing,
            priority=priority,
        )
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enqueue the corpus-wide learned sentence embedding backfill."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--only-missing",
        action="store_true",
        help="Enqueue only documents without active-model vectors (default).",
    )
    mode.add_argument(
        "--all",
        action="store_true",
        help="Re-enqueue every document that has sentences.",
    )
    parser.add_argument("--priority", type=int, default=100)
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("fatal: DATABASE_URL is not set", file=sys.stderr)
        return 2

    job_ids = asyncio.run(_run(database_url, only_missing=not args.all, priority=args.priority))
    print(f"semantic_embed: enqueued {len(job_ids)} job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
