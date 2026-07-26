"""Execution-telemetry tests: work-unit extraction, lane mapping, store safety.

All DB-free — the store is exercised against connection stubs, because the point
under test is that a telemetry failure never reaches the caller.
"""

from __future__ import annotations

from datetime import UTC, datetime

from doc_ray.adapters import telemetry_store
from doc_ray.domain import telemetry
from doc_ray.domain.jobs import LANE_CPU, LANE_GPU, lane_for_kind


def _record(**overrides: object) -> telemetry.ExecutionRecord:
    fields: dict[str, object] = {
        "job_id": "job-1",
        "kind": "annotate",
        "lane": LANE_GPU,
        "outcome": telemetry.OUTCOME_SUCCEEDED,
        "attempt": 1,
        "document_id": "doc-1",
        "worker_id": "gpu-box-1:42",
        "host": "gpu-box-1",
        "device": "cuda:NVIDIA H200",
        "work_units": 4150,
        "duration_ms": 75_000,
        "started_at": datetime(2026, 7, 25, 19, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return telemetry.ExecutionRecord(**fields)  # type: ignore[arg-type]


class _Transaction:
    def __enter__(self) -> _Transaction:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _RecordingConn:
    """Captures the insert a successful record produces."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def transaction(self) -> _Transaction:
        return _Transaction()

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.executed.append((sql, params))


class _BrokenConn(_RecordingConn):
    """A connection whose insert always fails (unmigrated DB, revoked grant)."""

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        raise RuntimeError('relation "job_telemetry" does not exist')


def test_record_execution_writes_every_field_in_column_order() -> None:
    conn = _RecordingConn()

    assert telemetry_store.record_execution(conn, _record()) is True

    sql, params = conn.executed[0]
    assert "INSERT INTO job_telemetry" in sql
    assert params == (
        "job-1",
        "annotate",
        LANE_GPU,
        "succeeded",
        1,
        "doc-1",
        "gpu-box-1:42",
        "gpu-box-1",
        "cuda:NVIDIA H200",
        4150,
        75_000,
        datetime(2026, 7, 25, 19, 0, tzinfo=UTC),
    )


def test_record_execution_swallows_a_write_failure() -> None:
    # Telemetry is accounting, not correctness: a job that ran must never fail
    # because its cost row could not be written.
    assert telemetry_store.record_execution(_BrokenConn(), _record()) is False


def test_failed_attempts_are_recordable_outcomes() -> None:
    # The 2026-07-25 OOM storm burned GPU seconds on 41 attempts that the
    # succeeding retry then erased from `jobs`. The record must survive it.
    record = _record(outcome=telemetry.OUTCOME_FAILED, work_units=None, attempt=3)
    conn = _RecordingConn()

    assert telemetry_store.record_execution(conn, record) is True
    assert conn.executed[0][1][3] == "failed"
    assert conn.executed[0][1][9] is None


def test_work_units_prefers_sentences_then_images() -> None:
    assert telemetry.work_units_from_result({"sentences": 120}) == 120
    assert telemetry.work_units_from_result({"images": 8}) == 8
    assert telemetry.work_units_from_result({"images": 8, "sentences": 120}) == 120


def test_work_units_is_none_for_results_that_report_no_size() -> None:
    assert telemetry.work_units_from_result({"document_id": "x"}) is None
    assert telemetry.work_units_from_result(None) is None
    assert telemetry.work_units_from_result("done") is None


def test_work_units_rejects_booleans_and_negatives() -> None:
    # bool is an int subclass; True would silently become "1 sentence".
    assert telemetry.work_units_from_result({"sentences": True}) is None
    assert telemetry.work_units_from_result({"sentences": -5}) is None


def test_lane_mapping_matches_the_worker_pool_split() -> None:
    for kind in ("annotate", "reannotate", "visual_extract", "semantic_embed", "visual_embed"):
        assert lane_for_kind(kind) == LANE_GPU
    for kind in ("ingest", "canonicalize", "semantic_enrich", "update_metadata"):
        assert lane_for_kind(kind) == LANE_CPU
