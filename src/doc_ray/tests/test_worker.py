"""Worker dispatch tests: run_once with stub + real handlers (skip without a DB).

run_once is the single claim->dispatch->finalize step. Tested with a stub registry
(success / raise) and the real delete handler (pure SQL). Test jobs use priority 0 so
they claim ahead of any default-priority stragglers, keeping assertions deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_ray.adapters import queue_postgres as q
from doc_ray.application.jobs import backoff_seconds, run_once
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


def test_backoff_growth_and_cap():
    assert backoff_seconds(1) == 5.0
    assert backoff_seconds(2) == 10.0
    assert backoff_seconds(3) == 20.0
    assert backoff_seconds(100) == 300.0  # capped


def test_run_once_dispatches_to_handler(qconn):
    conn, created = qconn
    seen: list[str] = []

    def stub(_c, _s, job):
        seen.append(job.id)
        return {"ok": True}

    job_id = q.enqueue(conn, "unit-test", {"x": 1}, priority=0)
    created.append(job_id)
    done = run_once(conn, None, worker_id="w1", handlers={"unit-test": stub})
    assert done is not None and done.id == job_id
    assert seen == [job_id]
    assert q.get(conn, job_id).status is JobStatus.SUCCEEDED


def test_run_once_unknown_kind_is_poison(qconn):
    conn, created = qconn
    job_id = q.enqueue(conn, "nonexistent-kind", {}, priority=0)
    created.append(job_id)
    run_once(conn, None, worker_id="w1", handlers={})
    assert q.get(conn, job_id).status is JobStatus.FAILED  # no retry


def test_run_once_handler_error_requeues(qconn):
    conn, created = qconn

    def boom(_c, _s, _job):
        raise ValueError("handler blew up")

    job_id = q.enqueue(conn, "boom", {}, priority=0)
    created.append(job_id)
    run_once(conn, None, worker_id="w1", handlers={"boom": boom})
    assert q.get(conn, job_id).status is JobStatus.QUEUED  # attempts 1 < 3 -> retry


def test_delete_handler_removes_document(qconn):
    import _itdb

    conn, created = qconn
    from doc_ray.application.jobs import HANDLERS

    doc_id = _itdb.make_doc(conn, "Delete me. Second sentence.")
    job_id = q.enqueue(conn, "delete", {}, priority=0, document_id=doc_id)
    created.append(job_id)

    run_once(conn, None, worker_id="w1", handlers=HANDLERS)

    gone = conn.execute("SELECT 1 FROM documents WHERE id = %s", (doc_id,)).fetchone()
    assert gone is None  # document (and its cascaded job) removed
