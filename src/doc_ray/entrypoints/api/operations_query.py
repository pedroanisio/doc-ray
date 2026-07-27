"""Admin reads for the operations console: what is outstanding, and what recurs."""

from __future__ import annotations

import strawberry

from doc_ray.application import operation_schedules, operations
from doc_ray.entrypoints.api.mutation_guards import _require_admin_read
from doc_ray.entrypoints.api.operations_types import Operation, OperationSchedule
from doc_ray.entrypoints.api.types import Info


@strawberry.type
class OperationQueries:
    """Operations status and schedule catalog. Admin-only, like the audit log."""

    @strawberry.field
    async def operations(self, info: Info, only_missing: bool = True) -> list[Operation]:
        """Every maintenance operation with its pending backlog and last outcome.

        ``only_missing`` mirrors the flag a run would use, so the number shown is
        the number that firing the operation would actually enqueue — a status
        view that counted differently from the runner would be worse than none.
        """
        ctx = info.context
        await _require_admin_read(ctx, action="query.operations")
        statuses = await operations.operation_status_async(ctx.conn, only_missing=only_missing)
        return [Operation.from_status(s) for s in statuses]

    @strawberry.field
    async def operation_schedules(self, info: Info) -> list[OperationSchedule]:
        """Recurring schedules, soonest-due first."""
        ctx = info.context
        await _require_admin_read(ctx, action="query.operation_schedules")
        rows = await operation_schedules.list_schedules(ctx.conn)
        return [OperationSchedule.from_row(row) for row in rows]
