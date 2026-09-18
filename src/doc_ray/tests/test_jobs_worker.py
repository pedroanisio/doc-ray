"""Job dispatch (application.jobs) ``run_once`` mechanics, on an isolated queue.

Split by concern (D-015): handler tests live in test_jobs_worker_handlers.py
and worker-loop tests in test_jobs_worker_loop.py, all sharing the TEMP-table
``jobs`` shadow from _jobs_worker_common.py.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any

import _itdb

from doc_ray.adapters import queue_postgres as queue
from doc_ray.application import jobs as jobs_mod
from doc_ray.domain.jobs import (
    Job,
    JobStatus,
)
from doc_ray.ports.queue import Queue


def test_backoff_grows_and_caps():
    assert jobs_mod.backoff_seconds(1) == 5.0
    assert jobs_mod.backoff_seconds(2) == 10.0
    assert jobs_mod.backoff_seconds(0) == 5.0
    assert jobs_mod.backoff_seconds(50) == 300.0  # capped


def test_queue_port_is_satisfied_by_the_postgres_adapter():
    # The Protocol names the adapter's contract; the module provides every method.
    for member in (
        "enqueue",
        "claim",
        "heartbeat",
        "update_progress",
        "complete",
        "fail",
        "reap_stale",
    ):
        assert callable(getattr(queue, member))
    assert isinstance(Queue, object)


def test_run_once_empty_queue_returns_none(qconn):
    assert jobs_mod.run_once(qconn, None, worker_id="t-empty") is None


def test_run_once_success_completes_job(qconn):
    seen: list[Job] = []

    def ok_handler(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        seen.append(job)
        return {"ok": True}

    jid = queue.enqueue(qconn, "stub", {"x": 1})
    got = jobs_mod.run_once(qconn, None, worker_id="t-ok", handlers={"stub": ok_handler})
    assert got is not None and got.id == jid
    assert seen and seen[0].payload == {"x": 1}
    row = qconn.execute("SELECT status, error FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row is not None and row["status"] == "succeeded"


class _UnitConn:
    def transaction(self):
        return contextlib.nullcontext()

    def execute(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("unit queue test should not execute SQL")


def test_run_once_logs_claim_and_success_lifecycle(monkeypatch, caplog):
    def ok_handler(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        return {"ok": True}

    job = Job(
        id="job-life",
        kind="stub",
        status=JobStatus.RUNNING,
        payload={"_meta": {"request_id": "req-life"}},
        attempts=1,
        max_attempts=3,
    )
    monkeypatch.setattr(jobs_mod.queue, "claim", lambda *_args, **_kwargs: job)
    monkeypatch.setattr(jobs_mod.queue, "update_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jobs_mod.queue, "is_canceled", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(jobs_mod.queue, "complete", lambda *_args, **_kwargs: True)

    with caplog.at_level(logging.INFO, logger="doc_ray.jobs"):
        jobs_mod.run_once(_UnitConn(), None, worker_id="t-life", handlers={"stub": ok_handler})

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "worker t-life claimed job job-life" in message
        and "kind=stub" in message
        and "attempt=1/3" in message
        and "request_id=req-life" in message
        for message in messages
    )
    assert any(
        "worker t-life completed job job-life" in message
        and "kind=stub" in message
        and "request_id=req-life" in message
        for message in messages
    )


def test_run_once_exposes_handler_progress(qconn):
    seen: list[dict[str, Any]] = []

    def progress_handler(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        jobs_mod.report_progress(2, 5, "inside handler")
        row = conn.execute(
            "SELECT progress_current, progress_total, progress_stage FROM jobs WHERE id=%s",
            (job.id,),
        ).fetchone()
        seen.append(dict(row))
        return {"ok": True}

    jid = queue.enqueue(qconn, "stub", {"x": 1})
    jobs_mod.run_once(qconn, None, worker_id="t-progress", handlers={"stub": progress_handler})

    assert seen == [
        {
            "progress_current": 2,
            "progress_total": 5,
            "progress_stage": "inside handler",
        }
    ]
    row = qconn.execute(
        "SELECT status, progress_current, progress_total, progress_stage FROM jobs WHERE id=%s",
        (jid,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "succeeded"
    assert row["progress_current"] == row["progress_total"] == 5
    assert row["progress_stage"] == "Completed"


def test_run_once_failure_requeues_with_backoff_then_terminal(qconn):
    def boom(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        raise RuntimeError("kaput")

    jid = queue.enqueue(qconn, "stub", {}, max_attempts=2)
    jobs_mod.run_once(qconn, None, worker_id="t-fail", handlers={"stub": boom})
    row = qconn.execute("SELECT status, error FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row is not None and row["status"] == "queued"  # retry scheduled (attempt 1/2)

    qconn.execute("UPDATE jobs SET run_after = now() WHERE id = %s", (jid,))
    jobs_mod.run_once(qconn, None, worker_id="t-fail", handlers={"stub": boom})
    row = qconn.execute("SELECT status, error FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row is not None and row["status"] == "failed"  # ceiling reached → terminal
    assert "kaput" in (row["error"] or "")


def test_run_once_failure_logs_exception_with_job_context(qconn, caplog):
    """BL-115: a handler failure must log the full traceback + job context, not
    just repr(exc) tucked away in the jobs.error DB column."""

    def boom(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        raise RuntimeError("kaput")

    jid = queue.enqueue(qconn, "stub", {"_meta": {"request_id": "req-worker-123"}}, max_attempts=2)
    with caplog.at_level("ERROR", logger="doc_ray.jobs"):
        jobs_mod.run_once(qconn, None, worker_id="t-fail-log", handlers={"stub": boom})
    assert caplog.records, "expected a log record for the failed handler"
    record = caplog.records[-1]
    assert record.exc_info is not None, "expected the traceback to be attached"
    assert jid in record.getMessage()
    assert "stub" in record.getMessage()
    assert "worker=t-fail-log" in record.getMessage()
    assert "request_id=req-worker-123" in record.getMessage()
    assert "retry in 5s" in record.getMessage()


def test_run_once_failure_logs_retry_lifecycle_without_database(monkeypatch, caplog):
    failures: list[dict[str, Any]] = []

    def boom(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        raise RuntimeError("kaput")

    job = Job(
        id="job-retry",
        kind="stub",
        status=JobStatus.RUNNING,
        payload={"_meta": {"request_id": "req-retry"}},
        attempts=1,
        max_attempts=2,
    )
    monkeypatch.setattr(jobs_mod.queue, "claim", lambda *_args, **_kwargs: job)
    monkeypatch.setattr(jobs_mod.queue, "update_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        jobs_mod.queue,
        "fail",
        lambda _conn, job_id, **kwargs: failures.append({"job_id": job_id, **kwargs}),
    )

    with caplog.at_level(logging.ERROR, logger="doc_ray.jobs"):
        jobs_mod.run_once(_UnitConn(), None, worker_id="t-retry", handlers={"stub": boom})

    assert failures == [
        {
            "job_id": "job-retry",
            "error": "RuntimeError('kaput')",
            "retry_in_seconds": 5.0,
        }
    ]
    record = caplog.records[-1]
    assert record.exc_info is not None
    assert "job-retry" in record.getMessage()
    assert "kind=stub" in record.getMessage()
    assert "worker=t-retry" in record.getMessage()
    assert "request_id=req-retry" in record.getMessage()
    assert "retry in 5s" in record.getMessage()


def test_run_once_times_out_hung_handler(qconn):
    def hung(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        time.sleep(1.0)
        return {"too": "late"}

    jid = queue.enqueue(qconn, "hung", {}, max_attempts=2)
    started = time.monotonic()

    jobs_mod.run_once(
        qconn,
        None,
        worker_id="t-timeout",
        handlers={"hung": hung},
        job_timeouts={"hung": 0.05},
    )

    assert time.monotonic() - started < 0.5
    row = qconn.execute("SELECT status, error FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row is not None
    assert row["status"] == "queued"
    assert "exceeded 0.05s execution timeout" in (row["error"] or "")


def test_run_once_unknown_kind_is_poison(qconn):
    jid = queue.enqueue(qconn, "mystery", {})
    got = jobs_mod.run_once(qconn, None, worker_id="t-poison", handlers={})
    assert got is not None and got.id == jid
    row = qconn.execute("SELECT status, error FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row is not None and row["status"] == "failed"
    assert "unknown job kind" in (row["error"] or "")


def test_run_once_unknown_kind_logs_poison_lifecycle(monkeypatch, caplog):
    failures: list[dict[str, Any]] = []
    job = Job(
        id="job-poison",
        kind="mystery",
        status=JobStatus.RUNNING,
        payload={"_meta": {"request_id": "req-poison"}},
        attempts=1,
        max_attempts=3,
    )
    monkeypatch.setattr(jobs_mod.queue, "claim", lambda *_args, **_kwargs: job)
    monkeypatch.setattr(
        jobs_mod.queue,
        "fail",
        lambda _conn, job_id, **kwargs: failures.append({"job_id": job_id, **kwargs}),
    )

    with caplog.at_level(logging.ERROR, logger="doc_ray.jobs"):
        jobs_mod.run_once(_UnitConn(), None, worker_id="t-poison-log", handlers={})

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "worker t-poison-log rejected job job-poison" in message
        and "unknown kind mystery" in message
        and "request_id=req-poison" in message
        for message in messages
    )
    assert failures == [
        {
            "job_id": "job-poison",
            "error": "unknown job kind 'mystery'",
            "retry_in_seconds": None,
        }
    ]


def test_run_once_heartbeat_thread_runs(qconn):
    hb_conn = _itdb.connect()
    try:
        release = threading.Event()

        def slow(conn: Any, store: Any, job: Job) -> dict[str, Any]:
            release.wait(0.25)  # keep the job in-flight across >1 heartbeat interval
            return {}

        jid = queue.enqueue(qconn, "slow", {})
        jobs_mod.run_once(
            qconn,
            None,
            worker_id="t-beat",
            handlers={"slow": slow},
            heartbeat_conn=hb_conn,
            heartbeat_interval=0.05,
        )
        row = qconn.execute("SELECT status, error FROM jobs WHERE id=%s", (jid,)).fetchone()
        assert row is not None and row["status"] == "succeeded"
    finally:
        hb_conn.close()


def test_run_once_heartbeat_requests_drain_when_shutdown_is_set(qconn, monkeypatch):
    """SIGTERM during a job: the heartbeat thread stamps the drain request once.

    The stamp is what lets the reaper tell a deploy-kill from a crash after the
    process is gone, so it must land while the handler is still running — and
    only once, not on every subsequent beat.
    """
    hb_conn = _itdb.connect()
    calls: list[tuple[str, str]] = []
    drained = threading.Event()

    def record_request_drain(conn: Any, job_id: str, *, worker_id: str) -> bool:
        calls.append((job_id, worker_id))
        drained.set()
        return True

    monkeypatch.setattr(jobs_mod.queue, "request_drain", record_request_drain)
    shutdown = threading.Event()
    shutdown.set()  # the deploy asked this worker to stop before the job finished

    def slow(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        assert drained.wait(5.0), "drain was not requested while the handler ran"
        time.sleep(0.15)  # outlive a few more beats: the stamp must not repeat
        return {}

    try:
        jid = queue.enqueue(qconn, "slow", {})
        jobs_mod.run_once(
            qconn,
            None,
            worker_id="t-drain",
            handlers={"slow": slow},
            heartbeat_conn=hb_conn,
            heartbeat_interval=0.02,
            shutdown=shutdown,
        )
        assert calls == [(jid, "t-drain")]
        row = qconn.execute("SELECT status FROM jobs WHERE id=%s", (jid,)).fetchone()
        assert row is not None and row["status"] == "succeeded"  # finished within grace
    finally:
        hb_conn.close()


def test_run_once_requests_drain_promptly_despite_long_heartbeat_interval(qconn, monkeypatch):
    """The drain stamp must not wait for the next heartbeat tick.

    The failure this pins: a deploy killed workers seconds after SIGTERM,
    while the first post-SIGTERM heartbeat was up to 15s away — the stamp never
    landed and every in-flight job burned its attempt as a "crash". The
    heartbeat thread must notice shutdown within ~1s regardless of interval.
    """
    hb_conn = _itdb.connect()
    drained = threading.Event()
    monkeypatch.setattr(
        jobs_mod.queue,
        "request_drain",
        lambda conn, job_id, *, worker_id: drained.set() or True,
    )
    shutdown = threading.Event()
    stamped_after = {}

    def slow(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        start = time.monotonic()
        shutdown.set()  # SIGTERM arrives mid-handler
        assert drained.wait(5.0), "drain stamp waited for the heartbeat tick"
        stamped_after["seconds"] = time.monotonic() - start
        return {}

    try:
        queue.enqueue(qconn, "slow", {})
        jobs_mod.run_once(
            qconn,
            None,
            worker_id="t-prompt",
            handlers={"slow": slow},
            heartbeat_conn=hb_conn,
            heartbeat_interval=30.0,  # a tick the stop-grace window never reaches
            shutdown=shutdown,
        )
        assert stamped_after["seconds"] < 5.0
    finally:
        hb_conn.close()


def test_run_once_heartbeat_does_not_request_drain_without_shutdown(qconn, monkeypatch):
    hb_conn = _itdb.connect()
    calls: list[str] = []
    monkeypatch.setattr(
        jobs_mod.queue,
        "request_drain",
        lambda conn, job_id, *, worker_id: calls.append(job_id) or True,
    )

    def slow(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        time.sleep(0.15)  # several beats with no shutdown requested
        return {}

    try:
        queue.enqueue(qconn, "slow", {})
        jobs_mod.run_once(
            qconn,
            None,
            worker_id="t-nodrain",
            handlers={"slow": slow},
            heartbeat_conn=hb_conn,
            heartbeat_interval=0.02,
            shutdown=threading.Event(),  # present but never set
        )
        assert calls == []
    finally:
        hb_conn.close()


def test_run_once_does_not_complete_job_canceled_during_handler(qconn, monkeypatch):
    def cancel_during_handler(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        conn.execute(
            "UPDATE jobs SET status = 'canceled', finished_at = now(), locked_by = NULL "
            "WHERE id = %s",
            (job.id,),
        )
        return {"ok": True}

    def complete_must_not_run(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("canceled jobs must not be completed")

    jid = queue.enqueue(qconn, "cancelable", {"chain": ["unreachable"]}, priority=-10_000)
    monkeypatch.setattr(jobs_mod.queue, "complete", complete_must_not_run)

    jobs_mod.run_once(
        qconn, None, worker_id="t-cancel", handlers={"cancelable": cancel_during_handler}
    )

    row = qconn.execute("SELECT status, result FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row is not None
    assert row["status"] == "canceled"
    assert row["result"] is None
    assert qconn.execute("SELECT 1 FROM jobs WHERE kind = 'unreachable'").fetchone() is None


def test_run_once_exclude_kinds_leaves_long_jobs_for_general_workers(qconn):
    def ok_handler(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        return {"ok": True}

    long_id = queue.enqueue(qconn, "stub_long", {})
    short_id = queue.enqueue(qconn, "stub_short", {})
    handlers = {"stub_long": ok_handler, "stub_short": ok_handler}

    got = jobs_mod.run_once(
        qconn, None, worker_id="t-fast", handlers=handlers, exclude_kinds=("stub_long",)
    )
    assert got is not None and got.id == short_id
    row = qconn.execute("SELECT status FROM jobs WHERE id=%s", (long_id,)).fetchone()
    assert row is not None and row["status"] == "queued"
