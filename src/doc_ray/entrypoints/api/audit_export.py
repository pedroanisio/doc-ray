"""Audit-event export endpoint: tenant-admin CSV/JSON downloads.

Split out of :mod:`doc_ray.entrypoints.api.app` (D-015). The endpoint runs on
a principal-bound pooled connection and requires the audit-admin guard before
reading any rows.
"""

from __future__ import annotations

import json
from csv import DictWriter
from io import StringIO
from typing import Any, cast

import psycopg
from fastapi import HTTPException, Request, Response
from psycopg_pool import AsyncConnectionPool

from doc_ray.adapters import security_audit_read
from doc_ray.entrypoints.api.security import (
    Authenticator,
    apply_principal,
    reset_principal,
)

_AUDIT_EXPORT_HEADERS = [
    "id",
    "occurred_at",
    "tenant_id",
    "actor_principal_id",
    "actor_subject",
    "action",
    "target_type",
    "target_id",
    "outcome",
    "source_ip",
    "request_id",
    "details",
]


def _jsonable_audit_event(row: dict[str, Any]) -> dict[str, Any]:
    """Return an audit event row with JSON-serializable scalar values."""
    out = {key: row.get(key) for key in _AUDIT_EXPORT_HEADERS}
    for key in ("id", "tenant_id", "actor_principal_id"):
        if out[key] is not None:
            out[key] = str(out[key])
    if out["occurred_at"] is not None:
        out["occurred_at"] = out["occurred_at"].isoformat()
    return out


def _audit_events_json(rows: list[dict[str, Any]]) -> str:
    """Serialize audit rows as a stable JSON export payload."""
    return json.dumps(
        {"events": [_jsonable_audit_event(row) for row in rows]},
        sort_keys=True,
        separators=(",", ":"),
    )


def _audit_events_csv(rows: list[dict[str, Any]]) -> str:
    """Serialize audit rows as CSV with a stable header order."""
    buf = StringIO()
    writer = DictWriter(buf, fieldnames=_AUDIT_EXPORT_HEADERS)
    writer.writeheader()
    for row in rows:
        event = _jsonable_audit_event(row)
        event["details"] = json.dumps(event["details"], sort_keys=True)
        writer.writerow(event)
    return buf.getvalue()


async def _export_audit_events_endpoint(
    request: Request,
    format: str = "csv",
    actor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    occurred_after: str | None = None,
    occurred_before: str | None = None,
    source_ip: str | None = None,
    limit: int = 1_000,
    after: str | None = None,
) -> Response:
    """Export tenant-admin-visible audit events as CSV or JSON."""
    export_format = format.lower()
    if export_format not in {"csv", "json"}:
        raise HTTPException(status_code=400, detail="format must be csv or json")
    authenticator: Authenticator = request.app.state.authenticator
    principal = await authenticator.authenticate(request)
    if principal.principal_id is None:
        raise HTTPException(status_code=401, detail="authentication required")

    pool: AsyncConnectionPool = request.app.state.pool
    async with pool.connection() as raw_conn:
        conn = cast(psycopg.AsyncConnection[dict[str, Any]], raw_conn)
        await apply_principal(conn, principal)
        try:
            await security_audit_read.require_audit_admin(conn)
            rows = await security_audit_read.list_audit_events(
                conn,
                actor=actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                occurred_after=occurred_after,
                occurred_before=occurred_before,
                source_ip=source_ip,
                limit=limit,
                after=after,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        finally:
            await reset_principal(conn, principal)

    if export_format == "json":
        return Response(
            content=_audit_events_json(rows),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="audit-events.json"'},
        )
    return Response(
        content=_audit_events_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-events.csv"'},
    )
