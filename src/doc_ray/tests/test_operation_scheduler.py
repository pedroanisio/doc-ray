"""Scheduler tick semantics, pinned without a database.

The behaviours here are the ones that only misbehave in production: a failing
schedule that re-fires forever, a tick that advances without recording what it
did, or a claim held across a long enqueue. A fake connection lets each be
asserted deterministically rather than waited for.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from doc_ray.entrypoints import operation_scheduler

NOW = datetime(2026, 7, 27, 2, 0, tzinfo=UTC)


class FakeConn:
    """Records the ordering of transactions and statements."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.updates: list[dict[str, Any]] = []
        self.depth = 0

    @contextmanager
    def transaction(self):  # noqa: ANN201 — mirrors psycopg's context manager
        self.depth += 1
        self.events.append(f"begin:{self.depth}")
        try:
            yield self
        finally:
            self.events.append(f"commit:{self.depth}")
            self.depth -= 1

    def execute(self, sql: str, params: Any = None):  # noqa: ANN201
        if "UPDATE operation_schedule" in sql:
            self.events.append("mark_fired")
            self.updates.append(dict(params))
        return self


def _schedule(**over: Any) -> dict[str, Any]:
    base = {
        "id": "01SCHED",
        "operation_key": "visual_embed",
        "cadence": {"kind": "daily", "hour": 2, "minute": 0},
        "only_missing": True,
        "priority": 500,
    }
    base.update(over)
    return base


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    state: dict[str, Any] = {"claim": _schedule(), "jobs": ["j1", "j2", "j3"], "raise": None}

    def fake_claim(conn: Any, *, now: datetime) -> dict[str, Any] | None:
        return state["claim"]

    def fake_run(conn: Any, key: str, **kwargs: Any) -> list[str]:
        if state["raise"] is not None:
            raise state["raise"]
        state["run_kwargs"] = {"key": key, **kwargs}
        return state["jobs"]

    monkeypatch.setattr(operation_scheduler, "claim_due", fake_claim)
    monkeypatch.setattr(operation_scheduler.operations, "run_operation", fake_run)
    return state


class TestRunOnce:
    def test_no_due_schedule_is_a_quiet_noop(self, patched: dict[str, Any]) -> None:
        patched["claim"] = None
        conn = FakeConn()
        assert operation_scheduler.run_once(conn, now=NOW) == {
            "fired": None,
            "jobs": 0,
            "error": None,
        }
        assert conn.updates == []

    def test_a_firing_enqueues_and_records_the_count(self, patched: dict[str, Any]) -> None:
        conn = FakeConn()
        result = operation_scheduler.run_once(conn, now=NOW)
        assert result == {"fired": "01SCHED", "jobs": 3, "error": None}
        assert patched["run_kwargs"] == {
            "key": "visual_embed",
            "only_missing": True,
            "priority": 500,
        }
        assert conn.updates[-1]["count"] == 3

    def test_next_run_advances_past_the_current_firing(self, patched: dict[str, Any]) -> None:
        conn = FakeConn()
        operation_scheduler.run_once(conn, now=NOW)
        # 02:00 daily, fired at exactly 02:00 -> tomorrow, never the same instant.
        assert conn.updates[0]["next"] == datetime(2026, 7, 28, 2, 0, tzinfo=UTC)

    def test_the_claim_commits_before_the_work_begins(self, patched: dict[str, Any]) -> None:
        """Advancing first is what stops a long enqueue holding the row lock."""
        conn = FakeConn()
        operation_scheduler.run_once(conn, now=NOW)
        first_commit = conn.events.index("commit:1")
        first_mark = conn.events.index("mark_fired")
        assert first_mark < first_commit
        # A second, separate transaction records the outcome.
        assert conn.events.count("begin:1") >= 2

    def test_a_failing_operation_still_advances_the_schedule(self, patched: dict[str, Any]) -> None:
        """Otherwise one broken operation re-fires on every tick — a hot loop."""
        patched["raise"] = RuntimeError("encoder unavailable")
        conn = FakeConn()
        result = operation_scheduler.run_once(conn, now=NOW)
        assert result["fired"] == "01SCHED"
        assert result["jobs"] == 0
        assert "encoder unavailable" in str(result["error"])
        assert conn.updates[-1]["next"] == datetime(2026, 7, 28, 2, 0, tzinfo=UTC)

    def test_a_failure_reason_is_persisted_not_just_returned(self, patched: dict[str, Any]) -> None:
        patched["raise"] = RuntimeError("encoder unavailable")
        conn = FakeConn()
        operation_scheduler.run_once(conn, now=NOW)
        assert "encoder unavailable" in conn.updates[-1]["error"]
        assert conn.updates[-1]["count"] is None

    def test_a_successful_firing_clears_the_error(self, patched: dict[str, Any]) -> None:
        conn = FakeConn()
        operation_scheduler.run_once(conn, now=NOW)
        assert conn.updates[-1]["error"] is None

    def test_schedule_priority_is_honoured_over_the_operation_default(
        self, patched: dict[str, Any]
    ) -> None:
        patched["claim"] = _schedule(priority=900)
        conn = FakeConn()
        operation_scheduler.run_once(conn, now=NOW)
        assert patched["run_kwargs"]["priority"] == 900

    def test_an_interval_cadence_advances_by_its_period(self, patched: dict[str, Any]) -> None:
        patched["claim"] = _schedule(cadence={"kind": "interval", "everyMinutes": 90})
        conn = FakeConn()
        operation_scheduler.run_once(conn, now=NOW)
        assert conn.updates[0]["next"] == datetime(2026, 7, 27, 3, 30, tzinfo=UTC)
