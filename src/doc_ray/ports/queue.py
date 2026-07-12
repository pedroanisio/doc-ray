"""Port: durable background-job queue.

Fulfilled by :mod:`doc_ray.adapters.queue_postgres` (a Postgres-backed queue using
``SELECT ... FOR UPDATE SKIP LOCKED``). Defined as a Protocol so a different backend
(Redis, SQS, ...) could be substituted without touching the worker or services.
Methods take a live connection they do not own — the caller manages it.
"""

from __future__ import annotations

from typing import Any, Protocol

import psycopg

from doc_ray.domain.jobs import Job


class Queue(Protocol):
    """Enqueue, claim, heartbeat, and finalize background jobs durably."""

    def enqueue(
        self,
        conn: psycopg.Connection[Any],
        kind: str,
        payload: dict[str, Any],
        *,
        priority: int = 100,
        max_attempts: int = 3,
        document_id: str | None = None,
    ) -> str:
        """Insert a queued job and return its new id."""
        ...

    def claim(self, conn: psycopg.Connection[Any], *, worker_id: str) -> Job | None:
        """Atomically claim the next eligible job for *worker_id*, or ``None``."""
        ...

    def heartbeat(self, conn: psycopg.Connection[Any], job_id: str, *, worker_id: str) -> bool:
        """Refresh a running job's liveness; ``False`` if it was reclaimed/canceled."""
        ...

    def is_canceled(self, conn: psycopg.Connection[Any], job_id: str) -> bool:
        """Return whether *job_id* is currently canceled."""
        ...

    def cancel(
        self, conn: psycopg.Connection[Any], job_id: str, *, reason: str | None = None
    ) -> bool:
        """Cancel a queued or running job, returning whether it transitioned."""
        ...

    def complete(
        self,
        conn: psycopg.Connection[Any],
        job_id: str,
        *,
        result: dict[str, Any] | None = None,
        worker_id: str | None = None,
    ) -> bool:
        """Mark a running job succeeded, recording an optional result."""
        ...

    def fail(
        self,
        conn: psycopg.Connection[Any],
        job_id: str,
        *,
        error: str,
        retry_in_seconds: float | None = None,
    ) -> None:
        """Fail a job: requeue with backoff if *retry_in_seconds* is set, else terminal."""
        ...

    def reap_stale(self, conn: psycopg.Connection[Any], *, older_than_seconds: float) -> int:
        """Reclaim (or fail, if exhausted) running jobs with a stale heartbeat."""
        ...
