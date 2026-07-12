"""Postgres queue tests against a live DB (skip if unreachable).

Ensures the ``jobs`` table by applying its own migration idempotently, so the test
does not depend on the migration runner having applied it and never touches other
migrations. Exercises the SKIP-LOCKED claim semantics, retry/backoff, and reaping.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from doc_ray.adapters import queue_postgres as q
from doc_ray.domain.jobs import JobStatus

_MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"


def _connect_or_skip():
    import _itdb

    try:
        conn = _itdb.connect()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {exc}")
    conn.execute((_MIGRATIONS / "020_jobs.sql").read_text())  # idempotent
    conn.execute((_MIGRATIONS / "059_job_progress.sql").read_text())  # idempotent
    return conn


@pytest.fixture
def qconn():
    conn = _connect_or_skip()
    created: list[str] = []
    try:
        yield conn, created
    finally:
        for job_id in created:
            conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
        conn.close()


def test_enqueue_claim_complete(qconn):
    conn, created = qconn
    job_id = q.enqueue(conn, "ingest", {"path": "/x"})
    created.append(job_id)

    job = q.claim(conn, worker_id="w1")
    assert job is not None
    assert job.id == job_id
    assert job.status is JobStatus.RUNNING
    assert job.attempts == 1
    assert job.payload == {"path": "/x"}

    assert q.claim(conn, worker_id="w1") is None  # nothing left queued

    q.complete(conn, job_id, result={"document_id": "d1"})
    done = q.get(conn, job_id)
    assert done is not None and done.status is JobStatus.SUCCEEDED


def test_job_status_transitions_emit_notifications(qconn):
    import _itdb

    conn, created = qconn
    listener = _itdb.connect()
    listener.execute("LISTEN docray_job_events")
    try:
        job_id = q.enqueue(conn, "ingest", {"path": "/x"}, priority=-100_000)
        created.append(job_id)
        claimed = q.claim(conn, worker_id="w-events")
        assert claimed is not None
        assert claimed.id == job_id
        assert q.complete(conn, job_id, worker_id="w-events")

        events = [
            json.loads(n.payload)
            for n in listener.notifies(timeout=1.0, stop_after=3)
            if n.channel == "docray_job_events"
        ]
    finally:
        listener.close()

    assert [event["status"] for event in events] == ["queued", "running", "succeeded"]
    assert {event["job_id"] for event in events} == {job_id}
    assert all(event["kind"] == "ingest" for event in events)


def test_job_progress_update_persists_and_notifies(qconn):
    import _itdb

    conn, created = qconn
    listener = _itdb.connect()
    listener.execute("LISTEN docray_job_events")
    try:
        job_id = q.enqueue(conn, "ingest", {"path": "/x"}, priority=-100_000)
        created.append(job_id)
        claimed = q.claim(conn, worker_id="w-progress")
        assert claimed is not None and claimed.id == job_id

        assert q.update_progress(
            conn,
            job_id,
            current=3,
            total=10,
            stage="extracting page 3 of 10",
            worker_id="w-progress",
        )
        row = conn.execute(
            "SELECT progress_current, progress_total, progress_stage "
            "FROM jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
        events = [
            json.loads(n.payload)
            for n in listener.notifies(timeout=1.0, stop_after=3)
            if n.channel == "docray_job_events"
        ]
    finally:
        listener.close()

    assert row == {
        "progress_current": 3,
        "progress_total": 10,
        "progress_stage": "extracting page 3 of 10",
    }
    assert any(event["job_id"] == job_id and event["status"] == "running" for event in events)


def test_skip_locked_claims_are_disjoint(qconn):
    import _itdb

    conn, created = qconn
    a = q.enqueue(conn, "ingest", {})
    b = q.enqueue(conn, "ingest", {})
    created += [a, b]

    conn2 = _itdb.connect()
    try:
        j1 = q.claim(conn, worker_id="w1")
        j2 = q.claim(conn2, worker_id="w2")  # must not block, must differ
        assert j1 is not None and j2 is not None
        assert j1.id != j2.id
    finally:
        conn2.close()


def test_claim_returns_none_when_only_job_is_taken(qconn):
    import _itdb

    conn, created = qconn
    job_id = q.enqueue(conn, "ingest", {})
    created.append(job_id)

    conn2 = _itdb.connect()
    try:
        assert q.claim(conn, worker_id="w1").id == job_id
        assert q.claim(conn2, worker_id="w2") is None  # skipped, not blocked
    finally:
        conn2.close()


def test_fail_retry_then_terminal(qconn):
    conn, created = qconn
    job_id = q.enqueue(conn, "ingest", {})
    created.append(job_id)

    q.claim(conn, worker_id="w1")
    q.fail(conn, job_id, error="boom", retry_in_seconds=0.0)
    assert q.get(conn, job_id).status is JobStatus.QUEUED  # requeued for retry

    q.claim(conn, worker_id="w1")  # attempts now 2
    q.fail(conn, job_id, error="boom again", retry_in_seconds=None)
    assert q.get(conn, job_id).status is JobStatus.FAILED  # terminal


def test_cancel_transitions_queued_and_running_jobs(qconn):
    conn, created = qconn
    queued_id = q.enqueue(conn, "ingest", {}, priority=-10_000)
    running_id = q.enqueue(conn, "annotate", {}, priority=-9_999)
    created += [queued_id, running_id]

    assert q.cancel(conn, queued_id, reason="operator request") is True
    assert q.get(conn, queued_id).status is JobStatus.CANCELED
    claimed = q.claim(conn, worker_id="w-cancel")
    assert claimed is not None
    assert claimed.id == running_id

    assert q.cancel(conn, running_id, reason="operator request") is True
    assert q.get(conn, running_id).status is JobStatus.CANCELED
    assert q.heartbeat(conn, running_id, worker_id="w-cancel") is False
    assert q.cancel(conn, running_id, reason="again") is False


def test_heartbeat_and_reap_stale(qconn):
    conn, created = qconn
    job_id = q.enqueue(conn, "ingest", {})
    created.append(job_id)

    q.claim(conn, worker_id="w1")
    assert q.heartbeat(conn, job_id, worker_id="w1") is True
    assert q.heartbeat(conn, job_id, worker_id="someone-else") is False

    conn.execute(
        "UPDATE jobs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (job_id,)
    )
    assert q.reap_stale(conn, older_than_seconds=60) >= 1
    assert q.get(conn, job_id).status is JobStatus.QUEUED  # reclaimed
