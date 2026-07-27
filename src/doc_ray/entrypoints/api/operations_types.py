"""GraphQL types for corpus operations and their schedules."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import strawberry

from doc_ray.application.operations import OperationStatus
from doc_ray.domain.operation_cadence import Cadence


@strawberry.type
class OperationCadence:
    """A validated recurrence. Exactly one shape per ``kind``."""

    kind: str
    every_minutes: int | None = None
    hour: int | None = None
    minute: int | None = None
    weekday: int | None = None
    day_of_month: int | None = None
    description: str = ""

    @staticmethod
    def from_domain(cadence: Cadence) -> OperationCadence:
        """Project a validated domain cadence onto the wire type."""
        return OperationCadence(
            kind=cadence.kind,
            every_minutes=cadence.every_minutes,
            hour=cadence.hour,
            minute=cadence.minute,
            weekday=cadence.weekday,
            day_of_month=cadence.day_of_month,
            description=cadence.describe(),
        )


@strawberry.input
class OperationCadenceInput:
    """Cadence as submitted by the console; validated server-side before storage."""

    kind: str
    every_minutes: int | None = None
    hour: int | None = None
    minute: int | None = None
    weekday: int | None = None
    day_of_month: int | None = None

    def to_payload(self) -> dict[str, Any]:
        """Render the camelCase payload the domain parser validates."""
        return {
            "kind": self.kind,
            "everyMinutes": self.every_minutes,
            "hour": self.hour,
            "minute": self.minute,
            "weekday": self.weekday,
            "dayOfMonth": self.day_of_month,
        }


@strawberry.type
class Operation:
    """A corpus-wide maintenance operation and what it currently owes."""

    key: str
    label: str
    job_kind: str
    lane: str
    summary: str
    completeness: str
    expensive: bool
    default_priority: int
    pending_documents: int
    pending_units: int
    unit_label: str | None
    active_jobs: int
    last_status: str | None
    last_finished_at: datetime | None
    last_error: str | None

    @staticmethod
    def from_status(status: OperationStatus) -> Operation:
        """Flatten registry metadata and live backlog into one row."""
        op = status.operation
        return Operation(
            key=op.key,
            label=op.label,
            job_kind=op.job_kind,
            lane=op.lane,
            summary=op.summary,
            completeness=op.completeness,
            expensive=op.expensive,
            default_priority=op.default_priority,
            pending_documents=status.pending_documents,
            pending_units=status.pending_units,
            unit_label=status.unit_label,
            active_jobs=status.active_jobs,
            last_status=status.last_status,
            last_finished_at=status.last_finished_at,
            last_error=status.last_error,
        )


@strawberry.type
class OperationSchedule:
    """A recurring operation with its materialized next firing time."""

    id: strawberry.ID
    operation_key: str
    cadence: OperationCadence
    only_missing: bool
    priority: int
    enabled: bool
    note: str | None
    next_run_at: datetime
    last_run_at: datetime | None
    last_job_count: int | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def from_row(row: dict[str, Any]) -> OperationSchedule:
        """Build the wire type from a stored schedule row."""
        from doc_ray.domain.operation_cadence import parse_cadence

        return OperationSchedule(
            id=strawberry.ID(str(row["id"])),
            operation_key=row["operation_key"],
            cadence=OperationCadence.from_domain(parse_cadence(row["cadence"])),
            only_missing=row["only_missing"],
            priority=row["priority"],
            enabled=row["enabled"],
            note=row["note"],
            next_run_at=row["next_run_at"],
            last_run_at=row["last_run_at"],
            last_job_count=row["last_job_count"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@strawberry.type
class OperationRun:
    """The result of firing an operation: what was enqueued, and when it starts."""

    operation_key: str
    enqueued: int
    priority: int
    run_after: datetime | None
    job_ids: list[strawberry.ID]
