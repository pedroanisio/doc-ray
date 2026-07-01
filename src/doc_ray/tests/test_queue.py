"""Postgres queue tests against a live DB (skip if unreachable).

Ensures the ``jobs`` table by applying its own migration idempotently, so the test
does not depend on the migration runner having applied it and never touches other
migrations. Exercises the SKIP-LOCKED claim semantics, retry/backoff, and reaping.
"""

from __future__ import annotations

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
