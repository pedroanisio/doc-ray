"""Domain model for the async job queue (pure, storage-agnostic).

The ``jobs`` table (migration ``020_jobs.sql``) is the source of record; these are
the in-memory shapes the queue adapter maps rows to and from. ``status`` is a closed
lifecycle enum; ``kind`` is deliberately open text (validated by the worker's
dispatch table, not the schema) so new job kinds need no migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    """The closed lifecycle of a job (mirrors the table's CHECK constraint)."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


# Well-known job kinds. `kind` is open text at the storage layer; these are the
# kinds the worker's dispatch table understands today.
KIND_INGEST = "ingest"
KIND_REANNOTATE = "reannotate"
KIND_DELETE = "delete"
KIND_SEED_CANONICAL = "seed_canonical"


@dataclass(frozen=True)
class Job:
    """A claimed unit of background work.

    Attributes:
        id: ULID primary key (time-sortable).
        kind: Job kind; routed by the worker's dispatch table.
        status: Current lifecycle state.
        payload: Kind-specific input parameters.
        attempts: How many times this job has been claimed.
        max_attempts: Attempt ceiling before it is marked ``failed``.
        document_id: Owning document UUID, or ``None`` until known.
    """

    id: str
    kind: str
    status: JobStatus
    payload: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    max_attempts: int = 3
    document_id: str | None = None
