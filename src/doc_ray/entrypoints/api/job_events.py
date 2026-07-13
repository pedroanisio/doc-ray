"""Server-Sent Events stream for job status (BL-122/BL-123).

Split out of :mod:`doc_ray.entrypoints.api.app` (D-015), following the
``register_metrics_route`` sibling precedent. The stream LISTENs on the queue's
notification channel over an RLS-bound pooled connection, so callers only ever
see jobs their principal is allowed to read.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from psycopg_pool import AsyncConnectionPool

from doc_ray.adapters import queue_postgres as queue
from doc_ray.entrypoints.api.security import (
    Authenticator,
    Principal,
    apply_principal,
    reset_principal,
)

_JOB_EVENTS_KEEPALIVE_SECONDS = 15.0


def _clean_uuid(value: str) -> str | None:
    """Return canonical UUID text, or None for malformed request arguments."""
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _job_event_data(row: dict[str, Any]) -> dict[str, Any]:
    """Shape one jobs row as the SSE event payload."""
    document_id = row.get("document_id")
    return {
        "id": str(row["id"]),
        "kind": str(row["kind"]),
        "status": str(row["status"]),
        "documentId": str(document_id) if document_id is not None else None,
        "createdAt": row["created_at"].isoformat() if row.get("created_at") else None,
        "startedAt": row["started_at"].isoformat() if row.get("started_at") else None,
        "finishedAt": row["finished_at"].isoformat() if row.get("finished_at") else None,
        "attempts": int(row["attempts"]),
        "maxAttempts": int(row["max_attempts"]),
        "error": row.get("error"),
        "progressCurrent": row.get("progress_current"),
        "progressTotal": row.get("progress_total"),
        "progressStage": row.get("progress_stage"),
    }


def _sse_event(event: str, data: dict[str, Any]) -> str:
    """Encode one Server-Sent Event frame."""
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _job_matches_filters(
    row: dict[str, Any], *, job_id: str | None, document_id: str | None
) -> bool:
    """Return whether a job row passes the stream's id/document filters."""
    if job_id is not None and str(row["id"]) != job_id:
        return False
    return not (document_id is not None and str(row.get("document_id")) != document_id)


async def _job_event_stream(
    request: Request,
    *,
    principal: Principal,
    job_id: str | None,
    document_id: str | None,
    limit: int,
    once: bool,
) -> AsyncIterator[str]:
    """Yield RLS-filtered job rows as Server-Sent Events."""
    pool: AsyncConnectionPool = request.app.state.pool
    bounded_limit = max(1, min(limit, 200))

    async with pool.connection() as raw_conn:
        conn = cast(psycopg.AsyncConnection[dict[str, Any]], raw_conn)
        await apply_principal(conn, principal)
        try:
            await conn.execute(f"LISTEN {queue.JOB_EVENTS_CHANNEL}")
            if job_id is not None:
                row = await queue.status_async(conn, job_id)
                if row is not None and _job_matches_filters(
                    row, job_id=job_id, document_id=document_id
                ):
                    yield _sse_event("job", _job_event_data(row))
            else:
                rows = await queue.list_async(conn, document_id=document_id, limit=bounded_limit)
                for row in rows:
                    yield _sse_event("job", _job_event_data(row))
            if once:
                return

            while not await request.is_disconnected():
                received = False
                async for notify in conn.notifies(timeout=_JOB_EVENTS_KEEPALIVE_SECONDS):
                    received = True
                    if notify.channel != queue.JOB_EVENTS_CHANNEL:
                        continue
                    try:
                        payload = json.loads(notify.payload)
                    except json.JSONDecodeError:
                        continue
                    event_job_id = payload.get("job_id")
                    if not event_job_id:
                        continue
                    row = await queue.status_async(conn, str(event_job_id))
                    if row is None or not _job_matches_filters(
                        row, job_id=job_id, document_id=document_id
                    ):
                        continue
                    yield _sse_event("job", _job_event_data(row))
                if not received:
                    yield ": keep-alive\n\n"
        finally:
            await reset_principal(conn, principal)


def register_job_events_route(app: FastAPI) -> None:
    """Attach the ``/jobs/events`` SSE route to *app*."""

    @app.get("/jobs/events")
    async def job_events(
        request: Request,
        job_id: str | None = None,
        document_id: str | None = None,
        limit: int = 20,
        once: bool = False,
    ) -> StreamingResponse:
        """Stream RLS-filtered job status events as Server-Sent Events."""
        clean_document_id: str | None = None
        if document_id is not None:
            clean_document_id = _clean_uuid(document_id)
            if clean_document_id is None:
                raise HTTPException(status_code=400, detail="invalid document_id")
        authenticator: Authenticator = request.app.state.authenticator
        principal = await authenticator.authenticate(request)
        return StreamingResponse(
            _job_event_stream(
                request,
                principal=principal,
                job_id=job_id,
                document_id=clean_document_id,
                limit=limit,
                once=once,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
