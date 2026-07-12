"""Security audit-event writer for API access and authentication events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, TypeVar

import psycopg
from fastapi import Request

from doc_ray.entrypoints.api.security import Principal

AuditOutcome = str
_ALLOWED_OUTCOMES = {"success", "failure", "denied"}
_ConnT = TypeVar("_ConnT", covariant=True)


class _ConnectionContext(Protocol[_ConnT]):
    async def __aenter__(self) -> _ConnT: ...

    async def __aexit__(self, *exc: object) -> object: ...


class _AuditPool(Protocol[_ConnT]):
    def connection(self) -> _ConnectionContext[_ConnT]: ...


def source_ip_from_request(request: Request) -> str | None:
    """Return the best available source address for audit logging."""
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or None
    return request.client.host if request.client else None


def request_id_from_request(request: Request) -> str | None:
    """Return a caller-provided request/correlation id, when present."""
    return request.headers.get("x-request-id") or request.headers.get("x-correlation-id")


def audit_subject(principal: Principal | None, explicit: str | None = None) -> str | None:
    """Return a non-secret actor subject suitable for durable audit logs."""
    subject = explicit if explicit is not None else (principal.subject if principal else None)
    if subject and subject.startswith("token:"):
        return "token:<redacted>"
    return subject


async def record_audit_event(
    conn: psycopg.AsyncConnection[Any],
    *,
    principal: Principal | None,
    action: str,
    target_type: str,
    target_id: str | None,
    outcome: AuditOutcome,
    source_ip: str | None = None,
    request_id: str | None = None,
    details: Mapping[str, Any] | None = None,
    actor_subject: str | None = None,
) -> None:
    """Insert one append-only security audit event."""
    if outcome not in _ALLOWED_OUTCOMES:
        raise ValueError(f"invalid audit outcome: {outcome}")
    principal_id = principal.principal_id if principal else None
    payload = json.dumps(dict(details or {}), sort_keys=True, separators=(",", ":"))
    await conn.execute(
        """
        INSERT INTO audit_events
            (actor_principal_id, actor_subject, action, target_type, target_id,
             outcome, source_ip, request_id, details, tenant_id)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
             docray_audit_tenant_for_principal(%s::uuid))
        """,
        (
            principal_id,
            audit_subject(principal, actor_subject),
            action,
            target_type,
            target_id,
            outcome,
            source_ip,
            request_id,
            payload,
            principal_id,
        ),
    )


async def record_audit_event_from_pool(
    pool: _AuditPool[psycopg.AsyncConnection[Any]],
    *,
    principal: Principal | None,
    action: str,
    target_type: str,
    target_id: str | None,
    outcome: AuditOutcome,
    source_ip: str | None = None,
    request_id: str | None = None,
    details: Mapping[str, Any] | None = None,
    actor_subject: str | None = None,
) -> None:
    """Insert an audit event using a pool when the application lifespan is active."""
    if bool(getattr(pool, "closed", False)) or getattr(pool, "_opened", True) is False:
        return
    try:
        async with pool.connection() as conn:
            await record_audit_event(
                conn,
                principal=principal,
                action=action,
                target_type=target_type,
                target_id=target_id,
                outcome=outcome,
                source_ip=source_ip,
                request_id=request_id,
                details=details,
                actor_subject=actor_subject,
            )
    except (RuntimeError, psycopg.OperationalError):
        # Some unit tests exercise auth routes without entering FastAPI lifespan.
        # The real ASGI app opens the pool before serving traffic.
        return
