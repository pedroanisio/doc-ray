"""Postgres-backed job queue (engineering decisions D-004, D-005).

Claims jobs with ``SELECT ... FOR UPDATE SKIP LOCKED`` so any number of workers can
pull disjoint rows with no contention and no double-delivery. ``attempts`` is
incremented at claim time, so a worker that crashes mid-job still "spends" an
attempt — bounding poison-message retries. Crash recovery is a heartbeat plus
:func:`reap_stale`. The one exception to attempt-spending is a requested drain
(:func:`request_drain`): a worker killed *after* being asked to shut down was
stopped by a deploy, not by its job, so the reaper refunds that attempt.
Chosen over Redis/Celery because Postgres is already the system
of record: zero new infrastructure, and enqueue is transactional with the writes
that motivate it.

Functions are synchronous (the worker is a sync process running blocking work);
:func:`enqueue_async` is provided for the async API mutations. Both share the row
shape. Callers own the connection; nothing here opens, closes, or commits.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from doc_ray.correlation import payload_with_request_metadata
from doc_ray.domain.jobs import Job, JobStatus
from doc_ray.domain.ulid import new_ulid_str

_INSERT = (
    "INSERT INTO jobs (id, kind, status, priority, payload, max_attempts, document_id) "
    "VALUES (%s, %s, 'queued', %s, %s, %s, %s)"
)

_CLAIM = """
WITH picked AS (
    SELECT id FROM jobs
     WHERE status = 'queued' AND run_after <= now()
       AND (%(only_kinds)s::text[] IS NULL OR kind = ANY(%(only_kinds)s::text[]))
       AND (%(exclude_kinds)s::text[] IS NULL OR kind <> ALL(%(exclude_kinds)s::text[]))
     ORDER BY priority, run_after, id
     FOR UPDATE SKIP LOCKED
     LIMIT 1
)
UPDATE jobs j
   -- started_at is attempt-relative, not first-attempt-relative: ETA and
   -- runtime surfaces divide elapsed time by this attempt's progress, so a
   -- retry measured from a dead attempt's start reads hours old at zero
   -- progress. (No percent signs in this SQL string: psycopg parses them.)
   SET status = 'running', locked_by = %(worker_id)s, started_at = now(),
       heartbeat_at = now(), attempts = j.attempts + 1,
       -- A fresh attempt starts clean: a dead attempt's progress must not be
       -- displayed as this attempt's, and its drain stamp must not release us.
       drain_requested_at = NULL,
       progress_current = NULL, progress_total = NULL, progress_stage = NULL
  FROM picked
 WHERE j.id = picked.id
RETURNING j.id, j.kind, j.status, j.payload, j.attempts, j.max_attempts, j.document_id
        , j.progress_current, j.progress_total, j.progress_stage
"""

_SELECT = (
    "SELECT id, kind, status, payload, attempts, max_attempts, document_id, "
    "progress_current, progress_total, progress_stage FROM jobs WHERE id = %s"
)

JOB_EVENTS_CHANNEL = "docray_job_events"


def _event_payload(row: dict[str, Any]) -> str:
    doc = row.get("document_id")
    return json.dumps(
        {
            "job_id": str(row["id"]),
            "kind": str(row["kind"]),
            "status": str(row["status"]),
            "document_id": str(doc) if doc is not None else None,
            "progress_current": row.get("progress_current"),
            "progress_total": row.get("progress_total"),
            "progress_stage": row.get("progress_stage"),
        },
        separators=(",", ":"),
    )


def _notify_job_event(conn: psycopg.Connection[Any], row: dict[str, Any] | None) -> None:
    if row is None:
        return
    conn.execute("SELECT pg_notify(%s, %s)", (JOB_EVENTS_CHANNEL, _event_payload(row)))


async def _notify_job_event_async(
    conn: psycopg.AsyncConnection[Any], row: dict[str, Any] | None
) -> None:
    if row is None:
        return
    await conn.execute("SELECT pg_notify(%s, %s)", (JOB_EVENTS_CHANNEL, _event_payload(row)))


def _to_job(row: dict[str, Any]) -> Job:
    """Map a jobs row (dict_row) to a :class:`Job`."""
    doc = row.get("document_id")
    return Job(
        id=str(row["id"]),
        kind=row["kind"],
        status=JobStatus(row["status"]),
        payload=row.get("payload") or {},
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        document_id=str(doc) if doc is not None else None,
        progress_current=row.get("progress_current"),
        progress_total=row.get("progress_total"),
        progress_stage=row.get("progress_stage"),
    )


def enqueue(
    conn: psycopg.Connection[Any],
    kind: str,
    payload: dict[str, Any],
    *,
    priority: int = 100,
    max_attempts: int = 3,
    document_id: str | None = None,
) -> str:
    """Insert a queued job (sync) and return its new id."""
    job_id = new_ulid_str()
    payload = payload_with_request_metadata(payload)
    conn.execute(_INSERT, (job_id, kind, priority, Jsonb(payload), max_attempts, document_id))
    _notify_job_event(
        conn,
        {
            "id": job_id,
            "kind": kind,
            "status": JobStatus.QUEUED.value,
            "document_id": document_id,
            "progress_current": None,
            "progress_total": None,
            "progress_stage": None,
        },
    )
    return job_id


async def enqueue_async(
    conn: psycopg.AsyncConnection[Any],
    kind: str,
    payload: dict[str, Any],
    *,
    priority: int = 100,
    max_attempts: int = 3,
    document_id: str | None = None,
) -> str:
    """Insert a queued job (async, for the API) and return its new id."""
    job_id = new_ulid_str()
    payload = payload_with_request_metadata(payload)
    await conn.execute(_INSERT, (job_id, kind, priority, Jsonb(payload), max_attempts, document_id))
    await _notify_job_event_async(
        conn,
        {
            "id": job_id,
            "kind": kind,
            "status": JobStatus.QUEUED.value,
            "document_id": document_id,
            "progress_current": None,
            "progress_total": None,
            "progress_stage": None,
        },
    )
    return job_id


def claim(
    conn: psycopg.Connection[Any],
    *,
    worker_id: str,
    only_kinds: Sequence[str] | None = None,
    exclude_kinds: Sequence[str] | None = None,
) -> Job | None:
    """Atomically claim the next eligible queued job, or return ``None``.

    ``only_kinds``/``exclude_kinds`` restrict which kinds this claimer takes,
    so a dedicated worker can serve a short-job lane (e.g. everything except
    annotate/reannotate) while jobs it skips stay queued for its peers.
    """
    params = {
        "worker_id": worker_id,
        "only_kinds": list(only_kinds) if only_kinds else None,
        "exclude_kinds": list(exclude_kinds) if exclude_kinds else None,
    }
    row = conn.execute(_CLAIM, params).fetchone()
    _notify_job_event(conn, row)
    return _to_job(row) if row is not None else None


def get(conn: psycopg.Connection[Any], job_id: str) -> Job | None:
    """Return a job by id, or ``None`` if it does not exist."""
    row = conn.execute(_SELECT, (job_id,)).fetchone()
    return _to_job(row) if row is not None else None


async def status_async(conn: psycopg.AsyncConnection[Any], job_id: str) -> dict[str, Any] | None:
    """Return a job's status row for the API (id, kind, status, document_id, error, result)."""
    cur = await conn.execute(
        "SELECT id, kind, status, document_id, created_at, started_at, finished_at, "
        "attempts, max_attempts, error, result, "
        "progress_current, progress_total, progress_stage FROM jobs WHERE id = %s",
        (job_id,),
    )
    row: dict[str, Any] | None = await cur.fetchone()
    return row


def is_canceled(conn: psycopg.Connection[Any], job_id: str) -> bool:
    """Return whether *job_id* is currently canceled."""
    row = conn.execute("SELECT status = 'canceled' AS canceled FROM jobs WHERE id = %s", (job_id,))
    result = row.fetchone()
    return bool(result and result["canceled"])


def cancel(conn: psycopg.Connection[Any], job_id: str, *, reason: str | None = None) -> bool:
    """Cancel a queued or running job, returning whether it transitioned."""
    cur = conn.execute(
        "UPDATE jobs SET status = 'canceled', started_at = COALESCE(started_at, now()), "
        "finished_at = now(), error = %s, locked_by = NULL "
        "WHERE id = %s AND status IN ('queued', 'running') "
        "RETURNING id, kind, status, document_id, progress_current, progress_total, progress_stage",
        (reason or "canceled by operator", job_id),
    )
    row = cur.fetchone()
    _notify_job_event(conn, row)
    return row is not None


async def cancel_async(
    conn: psycopg.AsyncConnection[Any], job_id: str, *, reason: str | None = None
) -> dict[str, Any] | None:
    """Cancel a queued or running job and return its API status row."""
    cur = await conn.execute(
        "UPDATE jobs SET status = 'canceled', started_at = COALESCE(started_at, now()), "
        "finished_at = now(), error = %s, locked_by = NULL "
        "WHERE id = %s AND status IN ('queued', 'running') "
        "RETURNING id, kind, status, document_id, created_at, started_at, finished_at, "
        "attempts, max_attempts, error, result, "
        "progress_current, progress_total, progress_stage",
        (reason or "canceled by operator", job_id),
    )
    row: dict[str, Any] | None = await cur.fetchone()
    if row is not None:
        await _notify_job_event_async(conn, row)
        return row
    return await status_async(conn, job_id)


async def list_async(
    conn: psycopg.AsyncConnection[Any],
    *,
    document_id: str | None = None,
    limit: int = 20,
    after: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent job rows, newest first (optionally scoped to one document).

    The pipeline-progress read: clients poll this to render a document's
    ingest -> annotate -> canonicalize cascade.
    """
    cur = await conn.execute(
        "SELECT id, kind, status, document_id, created_at, started_at, finished_at, "
        "attempts, max_attempts, error, result, "
        "progress_current, progress_total, progress_stage "
        "FROM jobs WHERE (%s::uuid IS NULL OR document_id = %s) "
        "AND (%s::ulid IS NULL OR id < %s::ulid) ORDER BY id DESC LIMIT %s",
        (document_id, document_id, after, after, limit),
    )
    rows = await cur.fetchall()
    for row in rows:
        row["cursor"] = str(row["id"])
    return rows


def heartbeat(conn: psycopg.Connection[Any], job_id: str, *, worker_id: str) -> bool:
    """Refresh liveness; ``False`` if the job was reclaimed/canceled out from under us."""
    cur = conn.execute(
        "UPDATE jobs SET heartbeat_at = now() "
        "WHERE id = %s AND locked_by = %s AND status = 'running'",
        (job_id, worker_id),
    )
    return cur.rowcount > 0


def request_drain(conn: psycopg.Connection[Any], job_id: str, *, worker_id: str) -> bool:
    """Mark the running job we hold as draining: its worker was asked to shut down.

    Called from the heartbeat thread when the worker receives SIGTERM but the
    handler is still running. If the process is SIGKILLed before the handler
    finishes (deploy stop-grace expiry), :func:`reap_stale` sees the stamp and
    releases the job without spending an attempt — the deploy failed the worker,
    not the job. Cleared again on every fresh claim.

    Returns:
        Whether the stamp landed (``False`` if the claim was already lost).
    """
    cur = conn.execute(
        "UPDATE jobs SET drain_requested_at = now() "
        "WHERE id = %s AND locked_by = %s AND status = 'running'",
        (job_id, worker_id),
    )
    return cur.rowcount > 0


def update_progress(
    conn: psycopg.Connection[Any],
    job_id: str,
    *,
    current: int | None,
    total: int | None,
    stage: str | None,
    worker_id: str | None = None,
) -> bool:
    """Persist a running job's progress and emit a job-status notification."""
    cur = conn.execute(
        "UPDATE jobs SET progress_current = %s, progress_total = %s, progress_stage = %s, "
        "heartbeat_at = now() "
        "WHERE id = %s AND status = 'running' "
        "AND (%s::text IS NULL OR locked_by = %s) "
        "RETURNING id, kind, status, document_id, progress_current, progress_total, progress_stage",
        (current, total, stage, job_id, worker_id, worker_id),
    )
    row = cur.fetchone()
    _notify_job_event(conn, row)
    return row is not None


def complete(
    conn: psycopg.Connection[Any],
    job_id: str,
    *,
    result: dict[str, Any] | None = None,
    worker_id: str | None = None,
) -> bool:
    """Mark a running job succeeded, recording an optional result.

    With *worker_id* the update also requires holding the claim — a zombie
    worker whose job was reaped and reclaimed matches zero rows. Callers gate
    side effects (chain advancement) on the returned value.

    Returns:
        Whether this call actually transitioned the row.
    """
    cur = conn.execute(
        "UPDATE jobs SET status = 'succeeded', finished_at = now(), result = %s, "
        "progress_total = COALESCE(progress_total, progress_current, 1), "
        "progress_current = COALESCE(progress_total, progress_current, 1), "
        "progress_stage = COALESCE(progress_stage, 'Completed') "
        "WHERE id = %s AND status = 'running' "
        "AND (%s::text IS NULL OR locked_by = %s) "
        "RETURNING id, kind, status, document_id, progress_current, progress_total, progress_stage",
        (Jsonb(result) if result is not None else None, job_id, worker_id, worker_id),
    )
    row = cur.fetchone()
    _notify_job_event(conn, row)
    return row is not None


def fail(
    conn: psycopg.Connection[Any],
    job_id: str,
    *,
    error: str,
    retry_in_seconds: float | None = None,
) -> None:
    """Fail a job: requeue with backoff if *retry_in_seconds* is set, else terminal."""
    if retry_in_seconds is None:
        row = conn.execute(
            "UPDATE jobs SET status = 'failed', finished_at = now(), error = %s, locked_by = NULL "
            "WHERE id = %s "
            "RETURNING id, kind, status, document_id, "
            "progress_current, progress_total, progress_stage",
            (error, job_id),
        ).fetchone()
        _notify_job_event(conn, row)
        return
    row = conn.execute(
        "UPDATE jobs SET status = 'queued', run_after = now() + (%s * interval '1 second'), "
        "error = %s, locked_by = NULL WHERE id = %s "
        "RETURNING id, kind, status, document_id, progress_current, progress_total, progress_stage",
        (retry_in_seconds, error, job_id),
    ).fetchone()
    _notify_job_event(conn, row)


def reap_stale(conn: psycopg.Connection[Any], *, older_than_seconds: float) -> int:
    """Reclaim running jobs with a stale heartbeat; fail those out of attempts.

    Returns the number of rows released, reclaimed, or failed. This is crash
    recovery: a worker killed mid-job stops heartbeating, so its row is returned
    to the pool (or failed if it has already exhausted its attempts).

    A stale job whose worker had stamped a drain request is different: that
    worker was killed by a deploy after being asked to shut down, so the job is
    released with its claim-time attempt refunded and is never failed for it —
    otherwise a long job could be exhausted by max_attempts consecutive deploys
    without ever failing on its own.
    """
    cutoff = "now() - (%s * interval '1 second')"
    released_rows = conn.execute(
        f"UPDATE jobs SET status = 'queued', locked_by = NULL, run_after = now(), "
        f"attempts = GREATEST(attempts - 1, 0), drain_requested_at = NULL, "
        f"progress_current = NULL, progress_total = NULL, progress_stage = NULL, "
        f"error = 'released: worker shutdown during drain' "
        f"WHERE status = 'running' AND heartbeat_at < {cutoff} "
        f"AND drain_requested_at IS NOT NULL "
        f"RETURNING id, kind, status, document_id, "
        f"progress_current, progress_total, progress_stage",
        (older_than_seconds,),
    ).fetchall()
    for row in released_rows:
        _notify_job_event(conn, row)
    # Both crash branches must exclude drain-stamped rows, not merely run after
    # the release branch: every statement re-evaluates ``now()``, so a stamped
    # row crossing the staleness cutoff between one reap's release UPDATE and
    # another reap's requeue UPDATE (concurrent workers reap every loop) would
    # otherwise be caught by the stamp-blind branch and burn its attempt —
    # observed live 2026-07-12, one of four drained jobs lost the race.
    requeued_rows = conn.execute(
        f"UPDATE jobs SET status = 'queued', locked_by = NULL, run_after = now(), "
        f"progress_current = NULL, progress_total = NULL, progress_stage = NULL, "
        f"error = 'reclaimed: stale heartbeat' "
        f"WHERE status = 'running' AND heartbeat_at < {cutoff} AND attempts < max_attempts "
        f"AND drain_requested_at IS NULL "
        f"RETURNING id, kind, status, document_id, "
        f"progress_current, progress_total, progress_stage",
        (older_than_seconds,),
    ).fetchall()
    for row in requeued_rows:
        _notify_job_event(conn, row)
    exhausted_rows = conn.execute(
        f"UPDATE jobs SET status = 'failed', finished_at = now(), locked_by = NULL, "
        f"error = 'reclaimed: stale heartbeat, attempts exhausted' "
        f"WHERE status = 'running' AND heartbeat_at < {cutoff} AND attempts >= max_attempts "
        f"AND drain_requested_at IS NULL "
        f"RETURNING id, kind, status, document_id, "
        f"progress_current, progress_total, progress_stage",
        (older_than_seconds,),
    ).fetchall()
    for row in exhausted_rows:
        _notify_job_event(conn, row)
    return len(released_rows) + len(requeued_rows) + len(exhausted_rows)
