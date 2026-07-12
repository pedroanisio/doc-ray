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
from doc_ray.correlation import reset_current_request_id, set_current_request_id
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
    conn.execute((_MIGRATIONS / "072_jobs_drain_requested_at.sql").read_text())  # idempotent
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


def test_enqueue_stamps_current_request_id_into_payload_metadata(qconn):
    conn, created = qconn
    token = set_current_request_id("req-queue-123")
    try:
        job_id = q.enqueue(conn, "ingest", {"path": "/x"})
        created.append(job_id)
        job = q.get(conn, job_id)
    finally:
        reset_current_request_id(token)

    assert job is not None
    assert job.payload["_meta"]["request_id"] == "req-queue-123"


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
            "SELECT progress_current, progress_total, progress_stage FROM jobs WHERE id = %s",
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


def _backdate_heartbeat(conn, job_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (job_id,)
    )


def test_crash_reclaim_without_drain_still_burns_an_attempt(qconn):
    """A worker that dies with no shutdown warning spent its attempt (poison bound)."""
    conn, created = qconn
    job_id = q.enqueue(conn, "ingest", {}, priority=-50_000)
    created.append(job_id)

    q.claim(conn, worker_id="w1")  # attempts -> 1
    _backdate_heartbeat(conn, job_id)
    assert q.reap_stale(conn, older_than_seconds=60) >= 1

    row = conn.execute(
        "SELECT status, attempts, error FROM jobs WHERE id = %s", (job_id,)
    ).fetchone()
    assert row["status"] == "queued"
    assert row["attempts"] == 1  # the crash consumed the attempt
    assert row["error"] == "reclaimed: stale heartbeat"


def test_drained_stale_job_is_released_without_burning_an_attempt(qconn):
    """A deploy-kill (SIGTERM stamped, then SIGKILL) must not spend an attempt."""
    conn, created = qconn
    job_id = q.enqueue(conn, "ingest", {}, priority=-50_000)
    created.append(job_id)

    q.claim(conn, worker_id="w1")  # attempts -> 1
    assert q.request_drain(conn, job_id, worker_id="w1") is True
    _backdate_heartbeat(conn, job_id)
    assert q.reap_stale(conn, older_than_seconds=60) >= 1

    row = conn.execute(
        "SELECT status, attempts, drain_requested_at, error FROM jobs WHERE id = %s",
        (job_id,),
    ).fetchone()
    assert row["status"] == "queued"
    assert row["attempts"] == 0  # refunded: the deploy, not the job, failed
    assert row["drain_requested_at"] is None
    assert row["error"] == "released: worker shutdown during drain"


def test_drained_job_on_final_attempt_is_requeued_not_failed(qconn):
    """Deploys must never exhaust a job: even at attempts == max it is released."""
    conn, created = qconn
    job_id = q.enqueue(conn, "ingest", {}, max_attempts=1, priority=-50_000)
    created.append(job_id)

    q.claim(conn, worker_id="w1")  # attempts -> 1 == max_attempts
    assert q.request_drain(conn, job_id, worker_id="w1") is True
    _backdate_heartbeat(conn, job_id)
    assert q.reap_stale(conn, older_than_seconds=60) >= 1

    row = conn.execute("SELECT status, attempts FROM jobs WHERE id = %s", (job_id,)).fetchone()
    assert row["status"] == "queued"  # not 'failed'
    assert row["attempts"] == 0


def test_request_drain_requires_the_claim_holder(qconn):
    conn, created = qconn
    job_id = q.enqueue(conn, "ingest", {}, priority=-50_000)
    created.append(job_id)

    assert q.request_drain(conn, job_id, worker_id="w1") is False  # not running yet
    q.claim(conn, worker_id="w1")
    assert q.request_drain(conn, job_id, worker_id="intruder") is False
    assert q.request_drain(conn, job_id, worker_id="w1") is True
    q.complete(conn, job_id, worker_id="w1")
    assert q.request_drain(conn, job_id, worker_id="w1") is False  # no longer running


def test_reclaim_measures_started_at_from_the_current_attempt(qconn):
    """ETA and runtime derive from started_at; preserving the first attempt's
    start made a fresh retry show hours of elapsed time and absurd ETAs
    (live 2026-07-12: a re-claimed job read "~8m left" seconds in)."""
    conn, created = qconn
    job_id = q.enqueue(conn, "ingest", {}, priority=-50_000)
    created.append(job_id)

    q.claim(conn, worker_id="w1")
    conn.execute(
        "UPDATE jobs SET started_at = now() - interval '2 hours', "
        "heartbeat_at = now() - interval '1 hour' WHERE id = %s",
        (job_id,),
    )
    assert q.reap_stale(conn, older_than_seconds=60) >= 1

    q.claim(conn, worker_id="w2")
    row = conn.execute(
        "SELECT started_at > now() - interval '10 minutes' AS fresh FROM jobs WHERE id = %s",
        (job_id,),
    ).fetchone()
    assert row["fresh"] is True


def test_reap_clears_stale_progress_on_released_and_requeued_rows(qconn):
    """A queued row must not display a dead attempt's percentage while it
    waits for a worker (release path and crash-requeue path alike)."""
    conn, created = qconn
    job_id = q.enqueue(conn, "ingest", {}, priority=-50_000)
    created.append(job_id)

    q.claim(conn, worker_id="w1")
    assert q.update_progress(conn, job_id, current=5, total=12, stage="Registering", worker_id="w1")
    assert q.request_drain(conn, job_id, worker_id="w1") is True
    _backdate_heartbeat(conn, job_id)
    assert q.reap_stale(conn, older_than_seconds=60) >= 1
    row = conn.execute(
        "SELECT status, progress_current, progress_total, progress_stage FROM jobs WHERE id = %s",
        (job_id,),
    ).fetchone()
    assert row == {
        "status": "queued",
        "progress_current": None,
        "progress_total": None,
        "progress_stage": None,
    }

    q.claim(conn, worker_id="w2")
    assert q.update_progress(conn, job_id, current=3, total=12, stage="Parsing", worker_id="w2")
    _backdate_heartbeat(conn, job_id)  # crash: no drain stamp this time
    assert q.reap_stale(conn, older_than_seconds=60) >= 1
    row = conn.execute(
        "SELECT status, progress_current, progress_total, progress_stage FROM jobs WHERE id = %s",
        (job_id,),
    ).fetchone()
    assert row == {
        "status": "queued",
        "progress_current": None,
        "progress_total": None,
        "progress_stage": None,
    }


def test_claim_resets_previous_attempt_progress_and_drain_marker(qconn):
    """A retry must not display the dead attempt's progress as its own."""
    conn, created = qconn
    job_id = q.enqueue(conn, "ingest", {}, priority=-50_000)
    created.append(job_id)

    q.claim(conn, worker_id="w1")
    assert q.update_progress(
        conn, job_id, current=5, total=12, stage="Registering spans", worker_id="w1"
    )
    assert q.request_drain(conn, job_id, worker_id="w1") is True
    _backdate_heartbeat(conn, job_id)
    assert q.reap_stale(conn, older_than_seconds=60) >= 1

    job = q.claim(conn, worker_id="w2")
    assert job is not None and job.id == job_id
    assert job.progress_current is None
    assert job.progress_total is None
    assert job.progress_stage is None

    row = conn.execute(
        "SELECT progress_current, progress_total, progress_stage, drain_requested_at "
        "FROM jobs WHERE id = %s",
        (job_id,),
    ).fetchone()
    assert row == {
        "progress_current": None,
        "progress_total": None,
        "progress_stage": None,
        "drain_requested_at": None,
    }


# ---------------------------------------------------------------------------
# Kind-filtered claiming — the short-job lane (2026-07-12 starvation incident:
# a seconds-long ingest queued behind hours-long reannotates because every
# worker claims every kind).
# ---------------------------------------------------------------------------


def test_claim_exclude_kinds_skips_long_jobs(qconn):
    conn, created = qconn
    long_id = q.enqueue(conn, "annotate", {"lang": "en"})
    created.append(long_id)
    short_id = q.enqueue(conn, "ingest", {"path": "/x"})
    created.append(short_id)

    # The annotate is older (would be claimed first unfiltered); the fast
    # lane must skip it and take the ingest.
    job = q.claim(conn, worker_id="fast", exclude_kinds=("annotate", "reannotate"))
    assert job is not None and job.id == short_id

    # Nothing else is claimable for the fast lane; the annotate stays queued
    # for a general worker.
    assert q.claim(conn, worker_id="fast", exclude_kinds=("annotate", "reannotate")) is None
    general = q.claim(conn, worker_id="general")
    assert general is not None and general.id == long_id


def test_claim_only_kinds_claims_nothing_else(qconn):
    conn, created = qconn
    ingest_id = q.enqueue(conn, "ingest", {"path": "/x"})
    created.append(ingest_id)

    assert q.claim(conn, worker_id="w", only_kinds=("backup",)) is None
    job = q.claim(conn, worker_id="w", only_kinds=("ingest", "backup"))
    assert job is not None and job.id == ingest_id


def test_claim_without_kind_filters_is_unchanged(qconn):
    conn, created = qconn
    jid = q.enqueue(conn, "annotate", {"lang": "en"})
    created.append(jid)
    job = q.claim(conn, worker_id="w")
    assert job is not None and job.id == jid
