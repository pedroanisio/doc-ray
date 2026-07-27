"""Durable CRUD for recurring operation schedules, plus the scheduler's claim.

The async half serves the GraphQL resolvers (create/edit/delete from the admin
console); the sync half serves the scheduler loop. They are not twins — the
console never claims and the scheduler never edits — so each side carries only
what it needs.

CONCURRENCY. ``claim_due`` uses ``FOR UPDATE SKIP LOCKED`` and the caller
advances ``next_run_at`` inside the same transaction, exactly as the jobs queue
claims work. Two scheduler replicas therefore cannot both fire one schedule, and
a scheduler that dies mid-tick releases its lock without consuming the tick.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from doc_ray.domain.operation_cadence import next_run_at, parse_cadence
from doc_ray.domain.operations import BACKFILL_PRIORITY, get_operation
from doc_ray.domain.ulid import new_ulid_str

_COLUMNS = (
    "id, operation_key, cadence, only_missing, priority, enabled, note, "
    "next_run_at, last_run_at, last_job_count, last_error, created_at, updated_at, created_by"
)

#: Elevation target for every write. Schedules are operations infrastructure, so
#: the table has no user-facing write policy (migration 144) — authorization
#: happens at the API boundary, and the write runs as the trusted batch role.
_BATCH_WRITER_ROLE = "docray_batch"


def row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a schedule row for transport (cadence stays a plain dict)."""
    out = dict(row)
    out["id"] = str(out["id"])
    if out.get("created_by") is not None:
        out["created_by"] = str(out["created_by"])
    return out


def _validated(operation_key: str, cadence: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Reject an unknown operation or an unhonourable cadence before any write."""
    get_operation(operation_key)
    return operation_key, parse_cadence(cadence).to_dict()


async def list_schedules(conn: psycopg.AsyncConnection[Any]) -> list[dict[str, Any]]:
    """Every schedule, soonest-due first. Admin-only by RLS (migration 144)."""
    cur = await conn.execute(
        f"SELECT {_COLUMNS} FROM operation_schedule ORDER BY next_run_at, id"  # noqa: S608
    )
    return [row_to_dict(r) for r in await cur.fetchall()]


async def create_schedule(
    conn: psycopg.AsyncConnection[Any],
    *,
    operation_key: str,
    cadence: dict[str, Any],
    only_missing: bool = True,
    priority: int = BACKFILL_PRIORITY,
    note: str | None = None,
    created_by: str | None = None,
    now: datetime,
) -> dict[str, Any]:
    """Insert a schedule with its first firing time already materialized."""
    key, cadence_payload = _validated(operation_key, cadence)
    first = next_run_at(parse_cadence(cadence_payload), now)
    schedule_id = new_ulid_str()
    async with conn.transaction():
        await conn.execute(f"SET LOCAL ROLE {_BATCH_WRITER_ROLE}")
        cur = await conn.execute(
            f"INSERT INTO operation_schedule "  # noqa: S608
            "(id, operation_key, cadence, only_missing, priority, note, next_run_at, created_by) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_COLUMNS}",
            (
                schedule_id,
                key,
                Jsonb(cadence_payload),
                only_missing,
                priority,
                note,
                first,
                created_by,
            ),
        )
        row = await cur.fetchone()
    assert row is not None  # RETURNING on a successful INSERT always yields a row
    return row_to_dict(row)


async def update_schedule(
    conn: psycopg.AsyncConnection[Any],
    schedule_id: str,
    *,
    cadence: dict[str, Any] | None = None,
    only_missing: bool | None = None,
    priority: int | None = None,
    enabled: bool | None = None,
    note: str | None = None,
    now: datetime,
) -> dict[str, Any] | None:
    """Patch a schedule. Returns None when the id does not exist.

    Changing the cadence re-materializes ``next_run_at`` from ``now``: an operator
    who moves a nightly sweep from 02:00 to 04:00 means the next one, not one more
    firing at the old time.
    """
    sets: list[str] = []
    params: list[Any] = []
    if cadence is not None:
        parsed = parse_cadence(cadence)
        sets += ["cadence = %s", "next_run_at = %s"]
        params += [Jsonb(parsed.to_dict()), next_run_at(parsed, now)]
    for column, value in (
        ("only_missing", only_missing),
        ("priority", priority),
        ("enabled", enabled),
        ("note", note),
    ):
        if value is not None:
            sets.append(f"{column} = %s")
            params.append(value)
    if not sets:
        cur = await conn.execute(
            f"SELECT {_COLUMNS} FROM operation_schedule WHERE id = %s",  # noqa: S608
            (schedule_id,),
        )
        row = await cur.fetchone()
        return row_to_dict(row) if row else None

    sets.append("updated_at = now()")
    params.append(schedule_id)
    async with conn.transaction():
        await conn.execute(f"SET LOCAL ROLE {_BATCH_WRITER_ROLE}")
        cur = await conn.execute(
            f"UPDATE operation_schedule SET {', '.join(sets)} "  # noqa: S608
            f"WHERE id = %s RETURNING {_COLUMNS}",
            tuple(params),
        )
        row = await cur.fetchone()
    return row_to_dict(row) if row else None


async def delete_schedule(conn: psycopg.AsyncConnection[Any], schedule_id: str) -> bool:
    """Delete a schedule; True when a row was removed."""
    async with conn.transaction():
        await conn.execute(f"SET LOCAL ROLE {_BATCH_WRITER_ROLE}")
        cur = await conn.execute(
            "DELETE FROM operation_schedule WHERE id = %s RETURNING id", (schedule_id,)
        )
        return await cur.fetchone() is not None


def claim_due(conn: psycopg.Connection[Any], *, now: datetime) -> dict[str, Any] | None:
    """Lock the soonest due schedule for this tick, or return None.

    MUST be called inside a transaction: the row lock is what stops a second
    scheduler from firing the same schedule, and it is only held until commit.
    """
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM operation_schedule "  # noqa: S608
        "WHERE enabled AND next_run_at <= %(now)s "
        "ORDER BY next_run_at, id FOR UPDATE SKIP LOCKED LIMIT 1",
        {"now": now},
    ).fetchone()
    return row_to_dict(row) if row else None


def mark_fired(
    conn: psycopg.Connection[Any],
    schedule_id: str,
    *,
    next_at: datetime,
    now: datetime,
    job_count: int | None,
    error: str | None,
) -> None:
    """Advance a schedule past this tick, recording what the firing did.

    ``next_at`` advances even when the firing FAILED. A schedule that stayed due
    after an error would re-fire on every scheduler pass, turning one broken
    operation into a hot loop; the error is recorded instead so the console can
    show it while the cadence carries on.
    """
    conn.execute(
        "UPDATE operation_schedule SET next_run_at = %(next)s, last_run_at = %(now)s, "
        "last_job_count = %(count)s, last_error = %(error)s, updated_at = now() "
        "WHERE id = %(id)s",
        {
            "next": next_at,
            "now": now,
            "count": job_count,
            "error": error,
            "id": schedule_id,
        },
    )
