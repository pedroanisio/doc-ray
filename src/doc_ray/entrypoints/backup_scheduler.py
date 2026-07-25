"""Scheduled backup enqueue and retention pruning."""

from __future__ import annotations

import argparse
import time
from typing import Any

import psycopg

from doc_ray.adapters import persistence as db
from doc_ray.adapters import queue_postgres as queue
from doc_ray.adapters.object_store import ObjectStore
from doc_ray.application import backup
from doc_ray.config import Config
from doc_ray.domain.jobs import KIND_BACKUP


def _active_backup_job(conn: psycopg.Connection[Any]) -> str | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE kind = %s AND status IN ('queued', 'running') "
        "ORDER BY id LIMIT 1",
        (KIND_BACKUP,),
    ).fetchone()
    return str(row["id"]) if row is not None else None


def run_once(
    conn: psycopg.Connection[Any],
    store: ObjectStore,
    *,
    keep_latest: int,
    max_age_days: int,
    note: str,
) -> dict[str, Any]:
    """Prune expired backups and enqueue one backup if no backup is active."""
    pruned = backup.prune_backups(
        conn,
        store,
        keep_latest=keep_latest,
        max_age_days=max_age_days,
    )
    active = _active_backup_job(conn)
    if active is not None:
        return {"job_id": None, "active_job_id": active, "pruned": pruned}
    job_id = queue.enqueue(conn, KIND_BACKUP, {"note": note}, document_id=None)
    return {"job_id": job_id, "active_job_id": None, "pruned": pruned}


def main(argv: list[str] | None = None) -> int:
    """Run the scheduled backup loop."""
    cfg = Config.from_env()
    parser = argparse.ArgumentParser(description="Schedule doc-ray backup jobs.")
    parser.add_argument("--once", action="store_true", help="Run one enqueue/prune pass and exit.")
    parser.add_argument(
        "--interval",
        type=int,
        default=cfg.backup_schedule_seconds,
        help="Seconds between scheduled backup enqueue attempts.",
    )
    parser.add_argument(
        "--keep-latest",
        type=int,
        default=cfg.backup_retention_count,
        help="Newest backup archives to keep regardless of age.",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=cfg.backup_retention_days,
        help="Prune archives older than this once they are outside keep-latest.",
    )
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")

    if not cfg.backup_enabled:
        # Exit 0 rather than idle-loop: under `restart: unless-stopped` a clean
        # exit leaves the container stopped instead of burning a scheduler slot
        # waking up to do nothing. Flipping DOCRAY_BACKUP_ENABLED back on and
        # recreating the service is what starts it again.
        print(
            "backup scheduler: disabled (DOCRAY_BACKUP_ENABLED=false); "
            "no backup will be enqueued or pruned",
            flush=True,
        )
        return 0

    store = ObjectStore(cfg)
    store.ensure_bucket()
    while True:
        with db.connect(cfg) as conn:
            result = run_once(
                conn,
                store,
                keep_latest=args.keep_latest,
                max_age_days=args.retention_days,
                note="scheduled backup",
            )
        print(
            "backup scheduler: "
            f"job_id={result['job_id']} active_job_id={result['active_job_id']} "
            f"pruned={len(result['pruned'])}",
            flush=True,
        )
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
