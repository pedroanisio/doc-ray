"""Read the status of corpus operations and run them.

Two callers with two concurrency models share this module: the GraphQL resolvers
(async, on an RLS-bound request connection elevated per-INSERT) and the scheduler
plus operator CLIs (sync, already connected as the trusted batch role). Rather
than let those drift into two definitions of what an operation does, both halves
read eligibility from :mod:`doc_ray.application.operation_selection` and differ
only in how they execute — the sync/async split is transport, not logic.

Nothing here bypasses the queue: running an operation means enqueuing ordinary
jobs that the ordinary workers claim. That is what keeps a browser-triggered
sweep bounded, resumable, cancellable, and visible on the Jobs page.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from doc_ray.adapters import queue_postgres as queue
from doc_ray.application.operation_selection import count_sql, selection_sql, unit_label
from doc_ray.domain.operations import OPERATIONS, Operation, get_operation


@dataclass(frozen=True, slots=True)
class OperationStatus:
    """What an operator needs to size a sweep before firing it."""

    operation: Operation
    pending_documents: int
    pending_units: int
    unit_label: str | None
    active_jobs: int
    #: Most recent terminal job for this kind, so the console can show whether the
    #: last attempt actually worked rather than only what is outstanding.
    last_finished_at: datetime | None
    last_status: str | None
    last_error: str | None


_ACTIVE_SQL = (
    "SELECT count(*) AS n FROM jobs WHERE kind = %(kind)s AND status IN ('queued', 'running')"
)

_LAST_SQL = (
    "SELECT status, finished_at, error FROM jobs "
    "WHERE kind = %(kind)s AND status IN ('succeeded', 'failed', 'canceled') "
    "ORDER BY id DESC LIMIT 1"
)


def _status_from_rows(
    op: Operation, counts: dict[str, Any], active: dict[str, Any], last: dict[str, Any] | None
) -> OperationStatus:
    return OperationStatus(
        operation=op,
        pending_documents=int(counts["documents"]),
        pending_units=int(counts["units"]),
        unit_label=unit_label(op.key),
        active_jobs=int(active["n"]),
        last_finished_at=last["finished_at"] if last else None,
        last_status=last["status"] if last else None,
        last_error=last["error"] if last else None,
    )


async def operation_status_async(
    conn: psycopg.AsyncConnection[Any], *, only_missing: bool = True
) -> list[OperationStatus]:
    """Status for every registered operation, in registry order."""
    out: list[OperationStatus] = []
    for op in OPERATIONS:
        sql, params = count_sql(op.key, only_missing=only_missing)
        counts = await (await conn.execute(sql, params)).fetchone()
        active = await (await conn.execute(_ACTIVE_SQL, {"kind": op.job_kind})).fetchone()
        last = await (await conn.execute(_LAST_SQL, {"kind": op.job_kind})).fetchone()
        assert counts is not None and active is not None  # aggregates always return a row
        out.append(_status_from_rows(op, counts, active, last))
    return out


def operation_status(
    conn: psycopg.Connection[Any], *, only_missing: bool = True
) -> list[OperationStatus]:
    """Sync twin of :func:`operation_status_async` for the CLI and scheduler."""
    out: list[OperationStatus] = []
    for op in OPERATIONS:
        sql, params = count_sql(op.key, only_missing=only_missing)
        counts = conn.execute(sql, params).fetchone()
        active = conn.execute(_ACTIVE_SQL, {"kind": op.job_kind}).fetchone()
        last = conn.execute(_LAST_SQL, {"kind": op.job_kind}).fetchone()
        assert counts is not None and active is not None
        out.append(_status_from_rows(op, counts, active, last))
    return out


def run_operation(
    conn: psycopg.Connection[Any],
    key: str,
    *,
    only_missing: bool = True,
    priority: int | None = None,
    run_after: datetime | None = None,
    enqueued_by: str | None = None,
) -> list[str]:
    """Enqueue one job per eligible document; return the new job ids.

    ``priority`` defaults to the operation's own background priority rather than
    the queue's 100, so a sweep fired from the console cannot starve user-facing
    pipeline work — the failure mode that makes a "helpful" button dangerous.
    """
    op = get_operation(key)
    sql, params = selection_sql(key, only_missing=only_missing)
    rows = conn.execute(sql, params).fetchall()
    return [
        queue.enqueue(
            conn,
            op.job_kind,
            {},
            priority=op.default_priority if priority is None else priority,
            document_id=str(row["id"]),
            enqueued_by=enqueued_by,
            run_after=run_after,
        )
        for row in rows
    ]


async def run_operation_async(
    conn: psycopg.AsyncConnection[Any],
    key: str,
    *,
    only_missing: bool = True,
    priority: int | None = None,
    run_after: datetime | None = None,
    enqueue_document: Callable[[str, int], Awaitable[str]] | None = None,
) -> list[str]:
    """Async twin of :func:`run_operation`.

    ``enqueue_document`` lets the GraphQL layer keep its correlation, billing, and
    audit plumbing while sharing this exact selection; without it the plain queue
    enqueue is used.
    """
    op = get_operation(key)
    effective_priority = op.default_priority if priority is None else priority
    sql, params = selection_sql(key, only_missing=only_missing)
    rows = await (await conn.execute(sql, params)).fetchall()
    job_ids: list[str] = []
    for row in rows:
        document_id = str(row["id"])
        if enqueue_document is not None:
            job_ids.append(await enqueue_document(document_id, effective_priority))
        else:
            job_ids.append(
                await queue.enqueue_async(
                    conn,
                    op.job_kind,
                    {},
                    priority=effective_priority,
                    document_id=document_id,
                    run_after=run_after,
                )
            )
    return job_ids
