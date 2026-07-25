"""Persist execution telemetry (migration 126) — append-only, never fatal.

Telemetry is accounting, not correctness: a job that ran must never fail because
its cost row could not be written. Every entry point here swallows its errors and
logs at debug, so an unmigrated database or a revoked grant degrades to "no cost
data" instead of "no pipeline".
"""

from __future__ import annotations

import logging
from typing import Any

from doc_ray.domain.telemetry import ExecutionRecord

_logger = logging.getLogger("doc_ray.telemetry")

_INSERT = """
INSERT INTO job_telemetry (
    job_id, kind, lane, outcome, attempt, document_id,
    worker_id, host, device, work_units, duration_ms, started_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def record_execution(conn: Any, record: ExecutionRecord) -> bool:
    """Append *record* to ``job_telemetry``; return whether it was written.

    Runs on its own transaction so a failure here cannot poison the caller's —
    the worker records telemetry after the job is already finalized, and a
    rolled-back accounting row must not take a completed job with it.

    Args:
        conn: Live database connection.
        record: The finished execution to record.

    Returns:
        ``True`` when the row was inserted, ``False`` when it was dropped.
    """
    try:
        with conn.transaction():
            conn.execute(
                _INSERT,
                (
                    record.job_id,
                    record.kind,
                    record.lane,
                    record.outcome,
                    record.attempt,
                    record.document_id,
                    record.worker_id,
                    record.host,
                    record.device,
                    record.work_units,
                    record.duration_ms,
                    record.started_at,
                ),
            )
        return True
    except Exception:
        _logger.debug(
            "dropped telemetry for job %s (kind=%s); pipeline unaffected",
            record.job_id,
            record.kind,
            exc_info=True,
        )
        return False
