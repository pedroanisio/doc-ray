"""Background worker: claim and run jobs (engineering decision D-005).

A synchronous process that runs the blocking machinery (PDF extraction, spaCy) which
must never touch the API event loop. Scale by running several processes — SELECT ...
FOR UPDATE SKIP LOCKED coordinates them at the database. Each loop reaps stale jobs
(crash recovery), claims one, and processes it with a heartbeat so long jobs are not
reclaimed mid-run; a janitor thread also reaps on a timer so orphaned jobs are
recovered even while every worker is busy inside a long handler. SIGTERM/SIGINT
trigger a graceful drain of the in-flight job; the drain is also stamped on the job
row, so if the process is SIGKILLed before the handler finishes (deploy stop-grace
expiry) the reaper releases the job back to the queue without spending an attempt —
deploys must never exhaust a job's retries.

    python -m doc_ray.entrypoints.worker
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
from types import FrameType

import psycopg

from doc_ray.adapters import persistence as db
from doc_ray.adapters import queue_postgres as queue
from doc_ray.adapters.object_store import ObjectStore
from doc_ray.application.jobs import run_once
from doc_ray.config import Config
from doc_ray.logging_setup import configure_logging

_logger = logging.getLogger("doc_ray.worker")

_POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "5"))
_HEARTBEAT_SECONDS = float(os.environ.get("WORKER_HEARTBEAT_SECONDS", "15"))
_CLAIM_TIMEOUT_SECONDS = float(os.environ.get("WORKER_CLAIM_TIMEOUT_SECONDS", "120"))
#: Backoff before reconnecting after a dropped connection (BL-101). Short enough
#: that a transient blip recovers quickly, long enough not to hot-loop against a
#: database that is genuinely still down.
_RECONNECT_BACKOFF_SECONDS = float(os.environ.get("WORKER_RECONNECT_BACKOFF_SECONDS", "2"))
#: Janitor cadence: how often stale jobs are reaped independently of the claim
#: loop. The between-jobs reap stops the moment this worker starts a long job,
#: so without the janitor an orphaned job waits for a free slot before being
#: recovered (observed live 2026-07-12: 45+ minutes with all workers busy).
_REAP_SECONDS = float(os.environ.get("WORKER_REAP_SECONDS", "30"))


def _kinds_from_env(name: str) -> tuple[str, ...] | None:
    """Parse a comma-separated job-kind list from the environment, or ``None``."""
    kinds = tuple(k.strip() for k in os.environ.get(name, "").split(",") if k.strip())
    return kinds or None


#: Kind filters for this worker's claims: a dedicated short-job lane sets
#: WORKER_EXCLUDE_KINDS=annotate,reannotate so user-facing jobs (ingest,
#: metadata edits, backups) never queue behind hours-long NLP runs.
_ONLY_KINDS = _kinds_from_env("WORKER_ONLY_KINDS")
_EXCLUDE_KINDS = _kinds_from_env("WORKER_EXCLUDE_KINDS")


def _reap_once(settings: Config) -> int:
    """One janitor tick: reap stale jobs on a fresh, short-lived connection.

    A fresh connection per tick (at most every ``_REAP_SECONDS``) is cheap and
    self-healing: a dropped connection affects one tick and the next one
    reconnects, with no coordination with the claim loop's connections.
    """
    with db.connect(settings) as conn:
        return queue.reap_stale(conn, older_than_seconds=_CLAIM_TIMEOUT_SECONDS)


def _janitor_loop(settings: Config, shutdown: threading.Event) -> None:
    """Reap stale jobs on a timer until *shutdown*, surviving any tick error."""
    while not shutdown.wait(_REAP_SECONDS):
        try:
            reaped = _reap_once(settings)
            if reaped:
                _logger.info("janitor reaped %d stale job(s)", reaped)
        except Exception:
            _logger.debug("janitor reap tick failed; retrying next tick", exc_info=True)


def run(cfg: Config | None = None, *, worker_id: str | None = None) -> int:
    """Run the claim/dispatch loop until SIGTERM/SIGINT.

    Args:
        cfg: Runtime configuration; defaults to :meth:`Config.from_env`.
        worker_id: Claim-holder id; defaults to ``<hostname>:<pid>``.

    Returns:
        Process exit code (0 on graceful shutdown).
    """
    settings = cfg or Config.from_env()
    wid = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    store = ObjectStore(settings)
    shutdown = threading.Event()

    def _request_shutdown(_signum: int, _frame: FrameType | None) -> None:
        shutdown.set()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    janitor = threading.Thread(
        target=_janitor_loop, args=(settings, shutdown), daemon=True, name="janitor"
    )
    janitor.start()

    # Two connections: one for claim/dispatch, one dedicated to the heartbeat thread.
    # A connection-level error (dropped network, Postgres restart) is caught at this
    # outer level: the broken pair is closed by the `with` on the way out, we back
    # off briefly, then reconnect — a transient blip must never crash the process
    # (BL-101). Only `psycopg.OperationalError` (connection/transport failures) is
    # retried; handler-level errors are already contained inside `run_once`.
    while not shutdown.is_set():
        try:
            with db.connect(settings) as conn, db.connect(settings) as hb_conn:
                while not shutdown.is_set():
                    queue.reap_stale(conn, older_than_seconds=_CLAIM_TIMEOUT_SECONDS)
                    job = run_once(
                        conn,
                        store,
                        worker_id=wid,
                        heartbeat_conn=hb_conn,
                        heartbeat_interval=_HEARTBEAT_SECONDS,
                        shutdown=shutdown,
                        only_kinds=_ONLY_KINDS,
                        exclude_kinds=_EXCLUDE_KINDS,
                    )
                    if job is None:
                        shutdown.wait(_POLL_SECONDS)  # idle: sleep, but wake on shutdown
        except psycopg.OperationalError:
            _logger.warning(
                "worker lost its database connection; reconnecting in %.1fs",
                _RECONNECT_BACKOFF_SECONDS,
                exc_info=True,
            )
            shutdown.wait(_RECONNECT_BACKOFF_SECONDS)
    janitor.join(timeout=2.0)  # wait() wakes on the shutdown event, so this is prompt
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the worker."""
    configure_logging()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
