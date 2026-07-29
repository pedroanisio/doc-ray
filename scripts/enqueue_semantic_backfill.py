#!/usr/bin/env python3
"""Operator CLI: enqueue the corpus-wide learned sentence re-embed.

This is the API-independent operational path for the Layer-20 hard cutover.
Take a corpus backup first, then enqueue and let the heavy worker pool drain the
jobs. The default is idempotent after successful embedding; ``--all`` forces a
re-embed of every document that has sentences.

RLS: ``documents`` and ``sentences`` carry forced row-level security (migration
106) and the application role does not hold BYPASSRLS, so this CLI's corpus
selection returns **zero rows** when run as the plain app role — and enqueueing
nothing is indistinguishable from "the backfill is already complete". This is an
operator-wide batch task, not a per-caller read, so it assumes ``docray_batch``
(the BYPASSRLS role migration 106 creates for exactly this) and then refuses to
proceed if the corpus is still invisible.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import psycopg
from psycopg.rows import dict_row

from doc_ray.application.semantic_embed import enqueue_semantic_embed_jobs

#: Role holding BYPASSRLS, created by migration 106 for worker/CLI batch work.
BATCH_ROLE = "docray_batch"


class CorpusInvisible(RuntimeError):
    """Raised when the corpus reads as empty, so a no-op cannot pass as success."""


async def _prepare_session(conn: Any) -> None:
    """Assume the batch role and assert the corpus is actually readable."""
    await conn.execute(f"SET ROLE {BATCH_ROLE}")
    cur = await conn.execute("SELECT count(*) FROM documents WHERE deleted_at IS NULL")
    row = await cur.fetchone()
    visible = int(row[0] if isinstance(row, tuple) else row["count"])
    if visible == 0:
        raise CorpusInvisible(
            "no documents are visible after assuming "
            f"{BATCH_ROLE} — refusing to report an empty backfill as success"
        )


async def _run(database_url: str, *, only_missing: bool, priority: int) -> list[str]:
    conn = await psycopg.AsyncConnection.connect(
        database_url,
        row_factory=dict_row,
        autocommit=True,
    )
    try:
        await _prepare_session(conn)
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

    try:
        job_ids = asyncio.run(_run(database_url, only_missing=not args.all, priority=args.priority))
    except CorpusInvisible as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        return 2
    print(f"semantic_embed: enqueued {len(job_ids)} job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
