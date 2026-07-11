"""Background worker: claim and run jobs (engineering decision D-005).

A synchronous process that runs the blocking machinery (PDF extraction, spaCy) which
must never touch the API event loop. Scale by running several processes — SELECT ...
FOR UPDATE SKIP LOCKED coordinates them at the database. Each loop reaps stale jobs
(crash recovery), claims one, and processes it with a heartbeat so long jobs are not
reclaimed mid-run. SIGTERM/SIGINT trigger a graceful drain of the in-flight job.

    python -m doc_ray.entrypoints.worker
"""

from __future__ import annotations

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

_POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "5"))
_HEARTBEAT_SECONDS = float(os.environ.get("WORKER_HEARTBEAT_SECONDS", "15"))
_CLAIM_TIMEOUT_SECONDS = float(os.environ.get("WORKER_CLAIM_TIMEOUT_SECONDS", "120"))
#: Backoff before reconnecting after a dropped connection (BL-101). Short enough
#: that a transient blip recovers quickly, long enough not to hot-loop against a
#: database that is genuinely still down.
_RECONNECT_BACKOFF_SECONDS = float(os.environ.get("WORKER_RECONNECT_BACKOFF_SECONDS", "2"))


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
                    )
                    if job is None:
                        shutdown.wait(_POLL_SECONDS)  # idle: sleep, but wake on shutdown
        except psycopg.OperationalError:
            shutdown.wait(_RECONNECT_BACKOFF_SECONDS)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the worker."""
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
