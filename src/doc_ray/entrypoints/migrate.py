"""Command-line entrypoint for the runtime migration runner (D-006).

Usage::

    python -m doc_ray.entrypoints.migrate            # apply pending migrations
    python -m doc_ray.entrypoints.migrate --dry-run  # list pending, apply nothing

Exit codes:

* ``0`` — success (migrations applied, or nothing pending).
* ``2`` — the database is unreachable.
* ``130`` / ``143`` — interrupted by SIGINT / SIGTERM (128 + signal number). The
  in-flight statement was canceled and the runner exited promptly and cleanly
  rather than being SIGKILLed (exit 137) after Docker's grace window. Every
  migration is idempotent and self-recording, so the canceled file is simply
  re-applied on the next start (issue #4).
"""

from __future__ import annotations

import argparse
import contextlib
import signal
import sys
import threading
from types import FrameType
from typing import Any

import psycopg

from doc_ray.adapters import migrate as migrate_mod
from doc_ray.config import Config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doc_ray.entrypoints.migrate",
        description="Apply pending database migrations recorded in schema_migrations.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List migrations that would be applied, then exit without applying.",
    )
    return parser


def _apply_with_signal_cancellation(cfg: Config) -> tuple[list[str], int | None]:
    """Apply pending migrations, cancelling the in-flight statement on a stop signal.

    A long backfill (e.g. ``040_chunk_surface_backfill.sql``) can run for minutes.
    Without this, a routine ``docker compose down``/stop escalates SIGTERM to
    SIGKILL mid-statement — the alarming ``exit 137`` of issue #4.

    The apply runs on a worker thread while the **main thread stays responsive to
    signals** (a Python signal handler only runs on the main thread, and cannot run
    while that thread is blocked in psycopg's C-level socket wait on a long
    statement). On SIGTERM/SIGINT the main-thread handler cancels the running
    statement through the live connection (``Connection.cancel()`` is safe
    cross-thread), so the worker's blocked apply raises ``QueryCanceled`` and the
    runner unwinds and exits promptly; the transaction rolls back and the
    (unrecorded, idempotent) migration resumes on the next start.

    Returns:
        ``(applied, signum)`` where ``applied`` is the filenames applied and
        ``signum`` is the signal that interrupted the run (``None`` if it completed).
    """
    interrupted: dict[str, int] = {}
    active: dict[str, psycopg.Connection[Any]] = {}
    result: dict[str, Any] = {}
    done = threading.Event()

    def _register(conn: psycopg.Connection[Any] | None) -> None:
        if conn is None:
            active.pop("conn", None)
        else:
            active["conn"] = conn

    def _worker() -> None:
        try:
            result["applied"] = migrate_mod.apply_migrations(cfg, on_connection=_register)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller below
            result["exc"] = exc
        finally:
            done.set()

    def _request_cancel(signum: int, _frame: FrameType | None) -> None:
        interrupted["signum"] = signum
        conn = active.get("conn")
        if conn is not None:
            # cancel() is safe from a signal handler and cross-thread; it aborts
            # the running statement, raising QueryCanceled on the worker's blocked
            # apply call. Best effort — we are already shutting down.
            with contextlib.suppress(Exception):
                conn.cancel()

    previous_term = signal.signal(signal.SIGTERM, _request_cancel)
    previous_int = signal.signal(signal.SIGINT, _request_cancel)
    worker = threading.Thread(target=_worker, name="migrate-apply", daemon=True)
    try:
        worker.start()
        # Poll so the main thread returns to the interpreter regularly and any
        # pending signal handler runs within one tick of the stop signal.
        while not done.wait(0.1):
            pass
        worker.join()
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)

    exc = result.get("exc")
    if exc is None:
        return result.get("applied", []), None
    # A cancellation surfaces as QueryCanceled (an OperationalError subclass). Treat
    # it as an interruption only when a stop signal actually arrived; otherwise it
    # is a genuine connectivity failure and must propagate to the exit-2 path.
    if isinstance(exc, psycopg.OperationalError) and interrupted:
        return [], interrupted["signum"]
    raise exc


def main(argv: list[str] | None = None) -> int:
    """Apply pending migrations (or list them with ``--dry-run``).

    Args:
        argv: Optional argument vector (defaults to ``sys.argv``).

    Returns:
        Process exit code: ``0`` on success, ``2`` if the database is unreachable,
        ``128 + signum`` (130/143) if interrupted by SIGINT/SIGTERM.
    """
    args = _build_parser().parse_args(argv)
    cfg = Config.from_env()

    if args.dry_run:
        try:
            with psycopg.connect(cfg.database_url, autocommit=True) as conn:
                pending = migrate_mod.pending_migrations(conn)
        except psycopg.OperationalError as exc:
            print(f"error: database unreachable: {exc}", file=sys.stderr)
            return 2
        for path in pending:
            print(f"pending {path.name}")
        print(f"{len(pending)} pending")
        return 0

    try:
        applied, signum = _apply_with_signal_cancellation(cfg)
    except psycopg.OperationalError as exc:
        print(f"error: database unreachable: {exc}", file=sys.stderr)
        return 2

    if signum is not None:
        print(
            f"interrupted by signal {signum}: canceled the in-flight migration. "
            "Migrations are idempotent and self-recording, so it will resume on the "
            "next start.",
            file=sys.stderr,
        )
        return 128 + signum

    for name in applied:
        print(f"applied {name}")
    print(f"{len(applied)} applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
