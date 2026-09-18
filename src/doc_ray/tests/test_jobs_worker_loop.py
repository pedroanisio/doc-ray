"""Worker loop tests: drain on SIGINT, janitor reap, crash recovery, main().

Split out of test_jobs_worker.py (D-015); runs on the same TEMP-table ``jobs``
shadow from _jobs_worker_common.py.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from typing import Any

import psycopg
import pytest

from doc_ray.entrypoints import worker as worker_mod


class _ConnectionContext:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


@pytest.fixture(autouse=True)
def _isolate_database(monkeypatch):
    monkeypatch.setattr(worker_mod.db, "connect", lambda settings: _ConnectionContext())


def test_worker_loop_drains_on_sigint(monkeypatch):
    calls = {"reap": 0, "run": 0}

    monkeypatch.setattr(
        worker_mod.queue,
        "reap_stale",
        lambda conn, older_than_seconds: calls.__setitem__("reap", calls["reap"] + 1),
    )
    monkeypatch.setattr(
        worker_mod,
        "run_once",
        lambda conn, store, **kw: calls.__setitem__("run", calls["run"] + 1),
    )
    monkeypatch.setattr(worker_mod, "ObjectStore", lambda cfg: None)
    monkeypatch.setattr(worker_mod, "_POLL_SECONDS", 0.05)

    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)
    timer = threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        assert worker_mod.main() == 0
    finally:
        timer.cancel()
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    assert calls["reap"] >= 1 and calls["run"] >= 1


def test_worker_loop_hands_its_shutdown_event_to_run_once(monkeypatch):
    """The drain stamp needs the loop's own shutdown event inside run_once.

    Without this wiring, SIGTERM stops the *loop* but the in-flight job can
    never learn it is draining — so a later SIGKILL still burns an attempt.
    """
    seen: dict[str, Any] = {}

    monkeypatch.setattr(worker_mod.queue, "reap_stale", lambda conn, older_than_seconds: None)

    def capture_run_once(conn: Any, store: Any, **kw: Any) -> None:
        seen.setdefault("shutdown", kw.get("shutdown"))
        return None

    monkeypatch.setattr(worker_mod, "run_once", capture_run_once)
    monkeypatch.setattr(worker_mod, "ObjectStore", lambda cfg: None)
    monkeypatch.setattr(worker_mod, "_POLL_SECONDS", 0.05)

    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)
    timer = threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        assert worker_mod.main() == 0
    finally:
        timer.cancel()
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    assert isinstance(seen.get("shutdown"), threading.Event)
    assert seen["shutdown"].is_set()  # the loop's live event, not a copy


def test_worker_janitor_reaps_while_a_job_is_running(monkeypatch):
    """A stale job must not wait for a busy worker's next between-jobs reap.

    The failure this pins: with every worker chewing an hours-long job,
    a drain-released orphan sat unreaped for most of an hour because reap_stale
    only ran between jobs. The janitor thread must keep reaping on a timer
    while the claim loop is occupied.
    """
    calls = {"reap": 0, "run": 0}
    monkeypatch.setattr(
        worker_mod, "_reap_once", lambda settings: calls.__setitem__("reap", calls["reap"] + 1)
    )
    monkeypatch.setattr(worker_mod.queue, "reap_stale", lambda conn, older_than_seconds: None)

    def busy_run_once(conn: Any, store: Any, **kw: Any) -> None:
        calls["run"] += 1
        time.sleep(0.4)  # one long job hogging the claim loop
        return None

    monkeypatch.setattr(worker_mod, "run_once", busy_run_once)
    monkeypatch.setattr(worker_mod, "ObjectStore", lambda cfg: None)
    monkeypatch.setattr(worker_mod, "_POLL_SECONDS", 0.05)
    monkeypatch.setattr(worker_mod, "_REAP_SECONDS", 0.05, raising=False)

    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)
    timer = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        assert worker_mod.main() == 0
    finally:
        timer.cancel()
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    assert calls["run"] >= 1
    assert calls["reap"] >= 3  # ticked repeatedly during the 0.4s "job"


def test_worker_janitor_tick_errors_never_crash_the_process(monkeypatch):
    calls = {"reap": 0, "run": 0}

    def broken_reap_once(settings: Any) -> None:
        calls["reap"] += 1
        raise RuntimeError("janitor tick exploded")

    monkeypatch.setattr(worker_mod, "_reap_once", broken_reap_once)
    monkeypatch.setattr(worker_mod.queue, "reap_stale", lambda conn, older_than_seconds: None)
    monkeypatch.setattr(
        worker_mod,
        "run_once",
        lambda conn, store, **kw: calls.__setitem__("run", calls["run"] + 1),
    )
    monkeypatch.setattr(worker_mod, "ObjectStore", lambda cfg: None)
    monkeypatch.setattr(worker_mod, "_POLL_SECONDS", 0.05)
    monkeypatch.setattr(worker_mod, "_REAP_SECONDS", 0.05, raising=False)

    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)
    timer = threading.Timer(0.4, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        assert worker_mod.main() == 0  # broken ticks must not take the worker down
    finally:
        timer.cancel()
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    assert calls["run"] >= 1
    assert calls["reap"] >= 2  # it kept trying after the failure


def test_worker_loop_recovers_from_dropped_connection(monkeypatch):
    """BL-101: a connection-level error must be retried, never crash the process."""
    calls = {"reap": 0, "run": 0}

    def flaky_reap_stale(_conn, older_than_seconds):
        calls["reap"] += 1
        if calls["reap"] == 1:
            raise psycopg.OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(worker_mod.queue, "reap_stale", flaky_reap_stale)
    monkeypatch.setattr(
        worker_mod,
        "run_once",
        lambda conn, store, **kw: calls.__setitem__("run", calls["run"] + 1),
    )
    monkeypatch.setattr(worker_mod, "ObjectStore", lambda cfg: None)
    monkeypatch.setattr(worker_mod, "_POLL_SECONDS", 0.05)
    monkeypatch.setattr(worker_mod, "_RECONNECT_BACKOFF_SECONDS", 0.05)

    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)
    timer = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        assert worker_mod.main() == 0  # never raises out of main() / process never crashes
    finally:
        timer.cancel()
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    # The dropped-connection reap_stale call was call #1; the loop must have
    # reconnected and gone on to claim/process at least one more job afterward.
    assert calls["reap"] >= 2
    assert calls["run"] >= 1


def test_main_configures_logging_before_running(monkeypatch):
    """BL-115: the worker process configures logging once at startup, before run()."""
    calls: list[str] = []
    monkeypatch.setattr(worker_mod, "configure_logging", lambda: calls.append("configured"))
    monkeypatch.setattr(worker_mod, "run", lambda: calls.append("ran") or 0)
    assert worker_mod.main() == 0
    assert calls == ["configured", "ran"]
