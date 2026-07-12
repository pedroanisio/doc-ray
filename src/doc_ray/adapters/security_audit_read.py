"""Read adapter for tenant-admin security audit searches."""

from __future__ import annotations

from typing import Any

import psycopg

AuditRow = dict[str, Any]

_MAX_AUDIT_LIMIT = 1_000
_DEFAULT_AUDIT_LIMIT = 100


def bounded_audit_limit(limit: int | None) -> int:
    """Return a safe audit result limit for search and export surfaces."""
    if limit is None:
        return _DEFAULT_AUDIT_LIMIT
    return max(1, min(int(limit), _MAX_AUDIT_LIMIT))


async def require_audit_admin(conn: psycopg.AsyncConnection[Any]) -> None:
    """Require the current principal to be a tenant admin."""
    cur = await conn.execute("SELECT docray_is_admin() AS ok")
    row = await cur.fetchone()
    if not row or not row["ok"]:
        raise PermissionError("audit events require an admin principal")


async def list_audit_events(
    conn: psycopg.AsyncConnection[Any],
    *,
    actor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    occurred_after: str | None = None,
    occurred_before: str | None = None,
    source_ip: str | None = None,
    limit: int | None = None,
    after: str | None = None,
) -> list[AuditRow]:
    """Return tenant-RLS-filtered audit events matching the supplied filters."""
    clauses: list[str] = []
    params: list[object] = []
    if actor:
        clauses.append("(actor_principal_id::text = %s OR actor_subject = %s)")
        params.extend([actor, actor])
    if action:
        clauses.append("action = %s")
        params.append(action)
    if target_type:
        clauses.append("target_type = %s")
        params.append(target_type)
    if target_id:
        clauses.append("target_id = %s")
        params.append(target_id)
    if occurred_after:
        clauses.append("occurred_at >= %s::timestamptz")
        params.append(occurred_after)
    if occurred_before:
        clauses.append("occurred_at < %s::timestamptz")
        params.append(occurred_before)
    if source_ip:
        clauses.append("source_ip = %s")
        params.append(source_ip)
    if after:
        clauses.append("id::text > %s")
        params.append(after)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(bounded_audit_limit(limit))
    cur = await conn.execute(
        f"""
        SELECT id, tenant_id, actor_principal_id, actor_subject, action, target_type,
               target_id, outcome, source_ip, request_id, details, occurred_at,
               id::text AS cursor
        FROM audit_events
        {where}
        ORDER BY occurred_at DESC, id DESC
        LIMIT %s
        """,
        tuple(params),
    )
    return list(await cur.fetchall())
