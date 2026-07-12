"""Job dispatch (application.jobs) + the worker loop, on an isolated queue.

Every test runs against a session-local ``TEMP TABLE jobs`` that shadows
``public.jobs`` via the temp schema's search-path precedence, so nothing here
can claim, complete, or fail another process's real queued work.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

import _itdb
import psycopg
import pytest

from doc_ray.adapters import queue_postgres as queue
from doc_ray.application import jobs as jobs_mod
from doc_ray.domain.jobs import (
    KIND_ADD_DOCUMENT_TAGS,
    KIND_DELETE,
    KIND_RESTORE_DOCUMENT,
    KIND_SUGGEST_DOCUMENT_METADATA,
    KIND_UPDATE_METADATA,
    Job,
    JobStatus,
)
from doc_ray.entrypoints import worker as worker_mod
from doc_ray.ports.queue import Queue

_MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"


@pytest.fixture()
def qconn():
    """A connection whose ``jobs`` table is a private temp shadow."""
    conn = _itdb.connect()
    conn.execute((_MIGRATIONS / "059_job_progress.sql").read_text())
    conn.execute((_MIGRATIONS / "072_jobs_drain_requested_at.sql").read_text())
    conn.execute("CREATE TEMP TABLE jobs (LIKE public.jobs INCLUDING ALL)")
    yield conn
    conn.close()


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

    Live failure (2026-07-12): a deploy killed workers seconds after SIGTERM,
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


def test_handle_delete_soft_deletes_document_and_records_history(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "To be deleted.")
    job = Job(id="0" * 26, kind=KIND_DELETE, status="queued", document_id=doc_id)  # type: ignore[arg-type]
    out = jobs_mod.handle_delete(qconn, None, job)
    assert out == {"deleted": doc_id, "soft": True}
    row = conn.execute(
        "SELECT lifecycle_state, deleted_at FROM documents WHERE id=%s", (doc_id,)
    ).fetchone()
    assert row["lifecycle_state"] == "deleted"
    assert row["deleted_at"] is not None
    history = conn.execute(
        "SELECT field, old_value, new_value FROM document_metadata_history "
        "WHERE document_id=%s ORDER BY id",
        (doc_id,),
    ).fetchall()
    assert [(r["field"], r["old_value"], r["new_value"]) for r in history] == [
        ("lifecycle_state", "active", "deleted")
    ]


def test_handle_restore_document_restores_soft_deleted_document(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "To be restored.")
    created.append(doc_id)
    jobs_mod.handle_delete(
        qconn,
        None,
        Job(id="0" * 26, kind=KIND_DELETE, status="queued", document_id=doc_id),  # type: ignore[arg-type]
    )
    job = Job(
        id="1" * 26,
        kind=KIND_RESTORE_DOCUMENT,
        status="queued",
        document_id=doc_id,
        payload={"actor": "tester"},
    )  # type: ignore[arg-type]

    out = jobs_mod.handle_restore_document(qconn, None, job)

    assert out == {"restored": doc_id}
    row = conn.execute(
        "SELECT lifecycle_state, deleted_at, archived_at FROM documents WHERE id=%s",
        (doc_id,),
    ).fetchone()
    assert row["lifecycle_state"] == "active"
    assert row["deleted_at"] is None
    assert row["archived_at"] is None
    history = conn.execute(
        "SELECT field, old_value, new_value, actor FROM document_metadata_history "
        "WHERE document_id=%s ORDER BY id",
        (doc_id,),
    ).fetchall()
    assert [(r["field"], r["old_value"], r["new_value"]) for r in history][-1:] == [
        ("lifecycle_state", "deleted", "active")
    ]
    assert history[-1]["actor"] == "tester"


def test_handle_add_document_tags_merges_tags_and_records_history(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "To be tagged.")
    created.append(doc_id)
    job = Job(
        id="0" * 26,
        kind=KIND_ADD_DOCUMENT_TAGS,
        status="queued",
        document_id=doc_id,
        payload={"tags": ["reviewed", "ops"], "actor": "bulk-user"},
    )  # type: ignore[arg-type]

    out = jobs_mod.handle_add_document_tags(qconn, None, job)

    assert out == {"changed": ["tags"]}
    tags = {
        r["tag"]
        for r in conn.execute(
            "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id=dt.tag_id "
            "WHERE dt.document_id=%s",
            (doc_id,),
        ).fetchall()
    }
    assert tags == {"reviewed", "ops"}
    history = conn.execute(
        "SELECT field, old_value, new_value, actor FROM document_metadata_history "
        "WHERE document_id=%s ORDER BY id",
        (doc_id,),
    ).fetchall()
    assert [(r["field"], r["old_value"], r["new_value"], r["actor"]) for r in history] == [
        ("tags", "", "ops, reviewed", "bulk-user")
    ]


def test_handle_suggest_document_metadata_persists_pending_suggestions(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Knowledge graph notes.")
    created.append(doc_id)
    conn.execute(
        "UPDATE documents SET title='upload', original_filename='knowledge-graph-notes.pdf' "
        "WHERE id=%s",
        (doc_id,),
    )
    job = Job(
        id="0" * 26,
        kind=KIND_SUGGEST_DOCUMENT_METADATA,
        status="queued",
        document_id=doc_id,
    )  # type: ignore[arg-type]

    out = jobs_mod.handle_suggest_document_metadata(qconn, None, job)

    assert out["suggested"] >= 1
    row = conn.execute(
        "SELECT field, status FROM document_metadata_suggestion WHERE document_id=%s",
        (doc_id,),
    ).fetchone()
    assert row["field"] == "title"
    assert row["status"] == "pending"


def test_handle_update_metadata_applies_edit_and_records_history(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Some text.")
    created.append(doc_id)
    job = Job(
        id="0" * 26,
        kind=KIND_UPDATE_METADATA,
        status="queued",
        document_id=doc_id,
        payload={"title": "New Title", "description": "A summary.", "tags": ["a", "b"]},
    )  # type: ignore[arg-type]

    out = jobs_mod.handle_update_metadata(qconn, None, job)

    assert set(out["changed"]) == {"title", "description", "tags"}
    row = conn.execute("SELECT title, description FROM documents WHERE id=%s", (doc_id,)).fetchone()
    assert row["title"] == "New Title"
    assert row["description"] == "A summary."
    tags = {
        r["tag"]
        for r in conn.execute(
            "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id=dt.tag_id "
            "WHERE dt.document_id=%s",
            (doc_id,),
        ).fetchall()
    }
    assert tags == {"a", "b"}
    history = conn.execute(
        "SELECT field FROM document_metadata_history WHERE document_id=%s ORDER BY id",
        (doc_id,),
    ).fetchall()
    assert [r["field"] for r in history] == ["title", "description", "tags"]


def test_handle_update_metadata_is_a_no_op_when_values_are_unchanged(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Some text.")
    created.append(doc_id)
    current_title = conn.execute("SELECT title FROM documents WHERE id=%s", (doc_id,)).fetchone()[
        "title"
    ]
    job = Job(
        id="0" * 26,
        kind=KIND_UPDATE_METADATA,
        status="queued",
        document_id=doc_id,
        payload={"title": current_title},
    )  # type: ignore[arg-type]

    out = jobs_mod.handle_update_metadata(qconn, None, job)

    assert out["changed"] == []
    history = conn.execute(
        "SELECT 1 FROM document_metadata_history WHERE document_id=%s", (doc_id,)
    ).fetchall()
    assert history == []


def test_handle_update_metadata_requires_document(qconn):
    job = Job(id="0" * 26, kind=KIND_UPDATE_METADATA, status="queued")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="document_id"):
        jobs_mod.handle_update_metadata(qconn, None, job)


def test_ports_declare_their_contracts():
    """The port modules are importable contract declarations (Protocol defs)."""
    import doc_ray.ports.canonical_repository as canonical_repository
    import doc_ray.ports.knowledge_base as knowledge_base
    import doc_ray.ports.resolver as resolver

    for mod in (resolver, knowledge_base, canonical_repository):
        names = [n for n in dir(mod) if not n.startswith("_")]
        assert names, f"{mod.__name__} declares nothing"


class _BlobStore:
    """Store double for handle_ingest: pre-seeded blob, content-addressed put."""

    def __init__(self) -> None:
        self.bucket = "test-bucket"
        self.blobs: dict[str, bytes] = {}

    def ensure_bucket(self) -> None:
        return None

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.blobs[key] = data
        return f"s3://{self.bucket}/{key}"

    def get(self, key: str) -> bytes:
        return self.blobs[key]


def test_handle_ingest_materializes_blob_and_ingests(qconn, pg):
    import hashlib

    conn, created = pg
    body = f"Ingested via a job. Unique {_itdb.unique_sha()[:8]}.".encode()
    sha = hashlib.sha256(body).hexdigest()
    store = _BlobStore()
    key = f"{sha[:2]}/{sha}.txt"
    store.blobs[key] = body

    job = Job(
        id="0" * 26,
        kind="ingest",
        status="queued",  # type: ignore[arg-type]
        payload={"storage_key": key, "extension": ".txt"},
    )
    out = jobs_mod.handle_ingest(qconn, store, job)
    created.append(out["document_id"])
    assert out["status"] == "ingested"


def test_handle_ingest_requires_store(qconn):
    job = Job(id="0" * 26, kind="ingest", status="queued", payload={})  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="object store"):
        jobs_mod.handle_ingest(qconn, None, job)


def test_run_once_ingest_rejects_blob_with_wrong_sha256(qconn):
    import hashlib

    expected = b"expected upload bytes"
    corrupt = b"corrupt upload bytes"
    expected_sha = hashlib.sha256(expected).hexdigest()
    key = f"{expected_sha[:2]}/{expected_sha}.txt"
    store = _BlobStore()
    store.blobs[key] = corrupt
    job_id = queue.enqueue(
        qconn,
        "ingest",
        {
            "storage_key": key,
            "sha256": expected_sha,
            "extension": ".txt",
            "original_filename": "corrupt.txt",
        },
        max_attempts=1,
    )

    jobs_mod.run_once(qconn, store, worker_id="t-integrity", handlers=jobs_mod.HANDLERS)

    row = qconn.execute("SELECT status, error FROM jobs WHERE id = %s", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert "sha256 mismatch" in row["error"]


def _seed_blob(store: _BlobStore, body: bytes, ext: str) -> str:
    """Content-address ``body`` into ``store`` and return its storage key."""
    import hashlib

    sha = hashlib.sha256(body).hexdigest()
    key = f"{sha[:2]}/{sha}{ext}"
    store.blobs[key] = body
    return key


def test_handle_ingest_title_falls_back_to_original_filename(qconn):
    """Without an explicit title, the document title is the real filename stem.

    Regression for the temp-name leak: before the fix the worker materialised the
    blob to a random ``tempfile`` name, so an upload without an explicit title took
    a ``tmp…`` scratch name as its title. The no-title path must fall back to the
    uploaded name (``Animal Farm``). The explicit-title path is covered by
    ``test_worker.test_upload_ingest_preserves_original_filename_and_title``.
    """
    body = f"No title supplied. {_itdb.unique_sha()[:8]}.".encode()
    store = _BlobStore()
    key = _seed_blob(store, body, ".txt")
    job = Job(
        id="0" * 26,
        kind="ingest",
        status="queued",  # type: ignore[arg-type]
        payload={"storage_key": key, "extension": ".txt", "original_filename": "Animal Farm.txt"},
    )
    out = jobs_mod.handle_ingest(qconn, store, job)
    row = qconn.execute(
        "SELECT title FROM documents WHERE id = %s", (out["document_id"],)
    ).fetchone()
    qconn.execute("DELETE FROM documents WHERE id = %s", (out["document_id"],))
    assert row["title"] == "Animal Farm"
    assert not row["title"].startswith("tmp")


def test_handle_reannotate_runs_spacy(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Elena founded a startup in Berlin.")
    created.append(doc_id)
    job = Job(id="0" * 26, kind="reannotate", status="queued", document_id=doc_id)  # type: ignore[arg-type]
    out = jobs_mod.handle_reannotate(qconn, None, job)
    assert out.get("occ", 0) > 0 or out.get("skipped") is True


def test_handle_reannotate_requires_document(qconn):
    job = Job(id="0" * 26, kind="reannotate", status="queued")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="document_id"):
        jobs_mod.handle_reannotate(qconn, None, job)


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

    Live failure (2026-07-12): with every worker chewing an hours-long job,
    a drain-released orphan sat unreaped for 45+ minutes because reap_stale
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
