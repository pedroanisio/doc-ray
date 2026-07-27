"""Admin writes for the operations console: run now, run later, and schedule.

Every resolver here is corpus-wide, so every one is admin-gated and audited. The
run path deliberately does NOT accept an arbitrary default priority: an operator
firing a sweep from a browser should not be able to starve the ingest pipeline by
leaving a field at its default, so the operation's own background priority is
used unless a value is passed explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import strawberry

from doc_ray.application import operation_schedules, operations
from doc_ray.domain.operation_cadence import CadenceError
from doc_ray.domain.operations import UnknownOperationError, get_operation
from doc_ray.entrypoints.api.mutation_guards import _audit_mutation_event, _require_admin
from doc_ray.entrypoints.api.operations_types import (
    OperationCadenceInput,
    OperationRun,
    OperationSchedule,
)
from doc_ray.entrypoints.api.types import Info


def _reject(exc: Exception) -> ValueError:
    """Surface a domain rejection as a clean GraphQL error, not a 500."""
    return ValueError(str(exc))


@strawberry.type
class OperationMutations:
    """Run and schedule corpus-wide maintenance operations."""

    @strawberry.mutation
    async def run_operation(
        self,
        info: Info,
        key: str,
        only_missing: bool = True,
        priority: int | None = None,
        run_after: datetime | None = None,
    ) -> OperationRun:
        """Enqueue an operation across the corpus; returns what was queued.

        ``run_after`` defers the whole sweep to an off-peak window without any
        scheduler involvement — the jobs are queued immediately but stay
        ineligible until then, so they are visible and cancellable meanwhile.
        """
        ctx = info.context
        await _require_admin(ctx, action="mutation.run_operation")
        try:
            operation = get_operation(key)
        except UnknownOperationError as exc:
            raise _reject(exc) from exc

        effective_priority = operation.default_priority if priority is None else priority
        job_ids = await operations.run_operation_async(
            ctx.conn,
            key,
            only_missing=only_missing,
            priority=effective_priority,
            run_after=run_after,
        )
        await _audit_mutation_event(
            ctx,
            action="mutation.run_operation",
            target_type="corpus",
            target_id=None,
            outcome="success",
            details={
                "operation": key,
                "onlyMissing": only_missing,
                "priority": effective_priority,
                "enqueued": len(job_ids),
                "runAfter": run_after.isoformat() if run_after else None,
            },
        )
        return OperationRun(
            operation_key=key,
            enqueued=len(job_ids),
            priority=effective_priority,
            run_after=run_after,
            job_ids=[strawberry.ID(job_id) for job_id in job_ids],
        )

    @strawberry.mutation
    async def create_operation_schedule(
        self,
        info: Info,
        key: str,
        cadence: OperationCadenceInput,
        only_missing: bool = True,
        priority: int | None = None,
        note: str | None = None,
    ) -> OperationSchedule:
        """Create a recurring schedule for an operation."""
        ctx = info.context
        await _require_admin(ctx, action="mutation.create_operation_schedule")
        try:
            operation = get_operation(key)
            row = await operation_schedules.create_schedule(
                ctx.conn,
                operation_key=key,
                cadence=cadence.to_payload(),
                only_missing=only_missing,
                priority=operation.default_priority if priority is None else priority,
                note=note,
                created_by=ctx.principal.principal_id,
                now=datetime.now(UTC),
            )
        except (CadenceError, UnknownOperationError) as exc:
            raise _reject(exc) from exc
        await _audit_mutation_event(
            ctx,
            action="mutation.create_operation_schedule",
            target_type="operation_schedule",
            target_id=str(row["id"]),
            outcome="success",
            details={"operation": key, "cadence": row["cadence"]},
        )
        return OperationSchedule.from_row(row)

    @strawberry.mutation
    async def update_operation_schedule(
        self,
        info: Info,
        schedule_id: strawberry.ID,
        cadence: OperationCadenceInput | None = None,
        only_missing: bool | None = None,
        priority: int | None = None,
        enabled: bool | None = None,
        note: str | None = None,
    ) -> OperationSchedule:
        """Edit a schedule. Changing the cadence re-arms the next firing."""
        ctx = info.context
        await _require_admin(ctx, action="mutation.update_operation_schedule")
        try:
            row = await operation_schedules.update_schedule(
                ctx.conn,
                str(schedule_id),
                cadence=cadence.to_payload() if cadence is not None else None,
                only_missing=only_missing,
                priority=priority,
                enabled=enabled,
                note=note,
                now=datetime.now(UTC),
            )
        except CadenceError as exc:
            raise _reject(exc) from exc
        if row is None:
            raise ValueError(f"no operation schedule with id {schedule_id}")
        await _audit_mutation_event(
            ctx,
            action="mutation.update_operation_schedule",
            target_type="operation_schedule",
            target_id=str(schedule_id),
            outcome="success",
            details={"enabled": row["enabled"], "cadence": row["cadence"]},
        )
        return OperationSchedule.from_row(row)

    @strawberry.mutation
    async def delete_operation_schedule(self, info: Info, schedule_id: strawberry.ID) -> bool:
        """Delete a schedule; False when it did not exist."""
        ctx = info.context
        await _require_admin(ctx, action="mutation.delete_operation_schedule")
        deleted = await operation_schedules.delete_schedule(ctx.conn, str(schedule_id))
        await _audit_mutation_event(
            ctx,
            action="mutation.delete_operation_schedule",
            target_type="operation_schedule",
            target_id=str(schedule_id),
            outcome="success" if deleted else "not_found",
        )
        return deleted
