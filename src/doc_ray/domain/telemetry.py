"""Domain model for execution telemetry (pure, storage-agnostic).

One :class:`ExecutionRecord` describes one finished attempt at one job: what ran,
where it ran, how long it took, and how much work it covered. It is the unit a
cost model prices — see ``db/migrations/126_job_telemetry.sql`` for why failed
attempts must be recorded alongside successful ones.

Nothing here touches a database or a GPU: the worker builds a record, an adapter
persists it, and ``scripts/cost_telemetry.py`` reads it back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED = "failed"
OUTCOME_CANCELED = "canceled"

#: Result keys that carry a size driver, in preference order per job kind. The
#: first key a handler's result actually reports wins, so a kind that changes
#: what it reports degrades to ``None`` rather than to a wrong number.
_WORK_UNIT_KEYS = ("sentences", "images", "documents", "mentions")


@dataclass(frozen=True)
class ExecutionRecord:
    """One finished execution attempt, priced as a unit.

    Attributes:
        job_id: The job this attempt belongs to.
        kind: Job kind.
        lane: Worker lane that paid for it (``gpu`` or ``cpu``).
        outcome: ``succeeded``, ``failed`` or ``canceled``.
        attempt: Attempt number, 1-based — retries are separate records.
        document_id: Document the work was for, when there is one.
        worker_id: Claim-holder identity (``host:pid``).
        host: Billed machine the work ran on.
        device: Compute device description (e.g. ``cuda:NVIDIA H200`` or ``cpu``).
        work_units: Size driver for the kind (sentences, images), when known.
        duration_ms: Wall-clock duration of the attempt.
        started_at: When the attempt began.
    """

    job_id: str
    kind: str
    lane: str
    outcome: str
    attempt: int
    document_id: str | None
    worker_id: str
    host: str
    device: str
    work_units: int | None
    duration_ms: int
    started_at: datetime


def work_units_from_result(result: Any) -> int | None:
    """Extract the size driver a handler reported, or ``None`` when it reported none.

    Args:
        result: A handler's return value; only mappings can carry a driver.

    Returns:
        The first recognised, non-negative integer count, else ``None``.
    """
    if not isinstance(result, dict):
        return None
    for key in _WORK_UNIT_KEYS:
        value = result.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None
