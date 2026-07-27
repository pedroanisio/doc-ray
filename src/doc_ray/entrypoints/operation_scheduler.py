"""Fire due operation schedules — the loop behind the console's Schedule tab.

Runs as its own service, exactly like ``backup_scheduler``: a scheduler that
shared the worker process would stop ticking whenever the worker was busy
draining a four-hour GPU job, which is precisely when a nightly sweep is due.

SAFETY. One tick fires at most one schedule. Claiming uses ``FOR UPDATE SKIP
LOCKED`` so replicas never double-fire, and ``next_run_at`` advances even when a
firing fails — a schedule that stayed due after an error would re-fire on every
pass and turn one broken operation into a hot loop.
"""

from __future__ import annotations

import argparse
import os
import time
import traceback
from datetime import UTC, datetime
from typing import Any

import psycopg

from doc_ray.adapters import persistence as db
from doc_ray.application import operations
from doc_ray.application.operation_schedules import claim_due, mark_fired
from doc_ray.config import Config
from doc_ray.domain.operation_cadence import next_run_at, parse_cadence

#: A schedule fires at minute granularity, so checking more often than this only
#: burns connections. Overridable for tests and for impatient operators.
DEFAULT_TICK_SECONDS = 60


def _default_interval() -> int:
    """Tick interval from the environment, falling back to the minute default.

    Read here rather than interpolated into the container's command line so the
    deployment has exactly one source of truth for the value, and so
    ``--interval`` still wins when an operator runs the loop by hand.
    """
    raw = os.environ.get("DOCRAY_OPERATION_SCHEDULER_INTERVAL", "60").strip()
    try:
        return int(raw)
    except ValueError:
        # An unparseable interval must not stop the service from scheduling at
        # all; fall back to the documented default rather than crash on boot.
        return DEFAULT_TICK_SECONDS


def run_once(conn: psycopg.Connection[Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Claim and fire at most one due schedule. Returns what happened."""
    moment = now or datetime.now(UTC)

    # PHASE 1 — claim and advance, committing before any work begins. Advancing
    # first is what makes a failing operation safe: the schedule leaves the due
    # window immediately, so an error cannot re-fire it on the very next tick.
    # It also keeps the row lock short. The cost is at-most-once delivery (a
    # crash between the phases skips one firing), which is the right trade for
    # idempotent, only-missing sweeps that the next tick picks up anyway.
    with conn.transaction():
        schedule = claim_due(conn, now=moment)
        if schedule is None:
            return {"fired": None, "jobs": 0, "error": None}
        schedule_id = str(schedule["id"])
        following = next_run_at(parse_cadence(schedule["cadence"]), moment)
        mark_fired(conn, schedule_id, next_at=following, now=moment, job_count=None, error=None)

    # PHASE 2 — enqueue outside the claim, then record the outcome. A schedule
    # that silently enqueues nothing is indistinguishable from one with no work
    # left, so both the count and the failure reason are written back.
    try:
        with conn.transaction():
            job_ids = operations.run_operation(
                conn,
                schedule["operation_key"],
                only_missing=schedule["only_missing"],
                priority=schedule["priority"],
            )
    except Exception as exc:  # noqa: BLE001 — a bad schedule must not kill the loop
        with conn.transaction():
            mark_fired(
                conn,
                schedule_id,
                next_at=following,
                now=moment,
                job_count=None,
                error=f"{type(exc).__name__}: {exc}",
            )
        return {"fired": schedule_id, "jobs": 0, "error": str(exc)}

    with conn.transaction():
        mark_fired(
            conn,
            schedule_id,
            next_at=following,
            now=moment,
            job_count=len(job_ids),
            error=None,
        )
    return {"fired": schedule_id, "jobs": len(job_ids), "error": None}


def _tick(cfg: Config) -> None:
    try:
        with db.connect(cfg) as conn:
            result = run_once(conn)
    except Exception:  # noqa: BLE001 — a transient DB outage must not end the service
        print("operation scheduler: tick failed\n" + traceback.format_exc(), flush=True)
        return
    if result["fired"] is not None:
        print(
            f"operation scheduler: fired schedule={result['fired']} "
            f"jobs={result['jobs']} error={result['error']}",
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    """Run the scheduler loop."""
    cfg = Config.from_env()
    parser = argparse.ArgumentParser(description="Fire due doc-ray operation schedules.")
    parser.add_argument("--once", action="store_true", help="Run one tick and exit.")
    parser.add_argument(
        "--interval",
        type=int,
        default=_default_interval(),
        help="Seconds between scheduler ticks (default: DOCRAY_OPERATION_SCHEDULER_INTERVAL).",
    )
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")

    while True:
        _tick(cfg)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
