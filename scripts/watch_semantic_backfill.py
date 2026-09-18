#!/usr/bin/env python3
"""Operator watch: log semantic_embed backfill progress until the queue drains.

Appends one status line per interval and exits 0 as soon as no `semantic_embed`
job is queued or running, so it terminates itself rather than needing a cron
entry that outlives the work.

RLS: `documents`, `sentences`, and `jobs` carry forced row-level security
(migration 106) and the application role does not hold BYPASSRLS, so counting as
that role returns zero for everything — which reads identically to "the backfill
is finished". This assumes `docray_batch` and refuses to start if the corpus is
still invisible, so an unreadable database cannot be logged as completion.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _work_dir import work_dir  # noqa: E402

#: Role holding BYPASSRLS, created by migration 106 for worker/CLI batch work.
BATCH_ROLE = "docray_batch"
#: Encoder whose vectors `semanticSearchSentences` actually queries.
ACTIVE_MODEL = "doc-ray/sbert-mpnet-768"

STATUS_SQL = """
SELECT count(*) FILTER (WHERE status = 'queued'),
       count(*) FILTER (WHERE status = 'running'),
       count(*) FILTER (WHERE status = 'succeeded'),
       count(*) FILTER (WHERE status = 'failed')
FROM jobs WHERE kind = 'semantic_embed'
"""

RATE_SQL = """
SELECT avg(extract(epoch FROM (finished_at - started_at)))
FROM jobs
WHERE kind = 'semantic_embed' AND status = 'succeeded'
  AND finished_at > now() - interval '2 hours'
"""

COVERAGE_SQL = """
SELECT (SELECT count(*) FROM documents
        WHERE status = 'extracted' AND deleted_at IS NULL),
       (SELECT count(DISTINCT sp.document_id) FROM embedding e
        JOIN spans sp ON sp.id = e.span_id WHERE e.model = %(model)s)
"""


def _one(conn: psycopg.Connection[Any], sql: str, params: Any = None) -> tuple[Any, ...]:
    """Run *sql* and return its single row."""
    row = conn.execute(sql, params).fetchone()
    if row is None:  # pragma: no cover - aggregate queries always return a row
        raise RuntimeError("expected a row")
    return tuple(row)


def sample(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """Return one progress sample: queue depth, coverage, and an ETA."""
    queued, running, succeeded, failed = _one(conn, STATUS_SQL)
    total, embedded = _one(conn, COVERAGE_SQL, {"model": ACTIVE_MODEL})
    avg = _one(conn, RATE_SQL)[0]
    eta_hours = (queued * float(avg) / 3600.0) if avg and queued else 0.0
    return {
        "queued": queued,
        "running": running,
        "succeeded": succeeded,
        "failed": failed,
        "documents": total,
        "embedded": embedded,
        "coverage": (embedded / total) if total else 0.0,
        "avg_seconds": round(float(avg), 1) if avg else None,
        "eta_hours": round(eta_hours, 1),
        "draining": bool(queued or running),
    }


def render(s: dict[str, Any]) -> str:
    """Render one sample as a single timestamped log line."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    if not s["draining"]:
        return (
            f"{stamp}  DRAINED  succeeded={s['succeeded']} failed={s['failed']}  "
            f"coverage={s['embedded']}/{s['documents']} ({s['coverage']:.1%})"
        )
    return (
        f"{stamp}  queued={s['queued']} running={s['running']} "
        f"succeeded={s['succeeded']} failed={s['failed']}  "
        f"coverage={s['embedded']}/{s['documents']} ({s['coverage']:.1%})  "
        f"avg={s['avg_seconds']}s  eta={s['eta_hours']}h"
    )


def _connect(database_url: str) -> psycopg.Connection[Any]:
    """Open a connection that can see the corpus, or fail loudly."""
    conn = psycopg.connect(database_url, autocommit=True)
    conn.execute(f"SET ROLE {BATCH_ROLE}")
    visible = _one(conn, "SELECT count(*) FROM documents WHERE deleted_at IS NULL")[0]
    if visible == 0:
        conn.close()
        raise RuntimeError(
            f"no documents visible after assuming {BATCH_ROLE}; refusing to log an "
            "unreadable corpus as a drained backfill"
        )
    return conn


def main(argv: list[str] | None = None) -> int:
    """Poll until the backfill drains, appending one line per interval."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between samples.")
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="status log (default: $DOCRAY_WORK_DIR/scratch/backfill-status.log)",
    )
    parser.add_argument("--max-hours", type=float, default=48.0, help="Give up after this long.")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("fatal: DATABASE_URL is not set", file=sys.stderr)
        return 2

    if args.log is None:
        args.log = work_dir("scratch") / "backfill-status.log"
    args.log.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.max_hours * 3600
    while True:
        try:
            conn = _connect(database_url)
            try:
                line = render(current := sample(conn))
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - a probe failure must be logged, not fatal
            stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
            line, current = f"{stamp}  ERROR  {type(exc).__name__}: {exc}", {"draining": True}
        with args.log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line, flush=True)
        if not current["draining"]:
            return 0
        if time.monotonic() >= deadline:
            print("fatal: gave up before the queue drained", file=sys.stderr)
            return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
