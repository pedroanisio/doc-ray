"""Cadence math for recurring operations: next_run_at is pure and total.

A schedule that silently drifts, fires twice, or never fires again is an
operational defect that only shows up in production weeks later, so every
branch of the recurrence is pinned here rather than exercised through the
scheduler loop.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from doc_ray.domain.operation_cadence import Cadence, CadenceError, next_run_at, parse_cadence


def _at(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


class TestParse:
    def test_interval_roundtrips(self) -> None:
        cadence = parse_cadence({"kind": "interval", "everyMinutes": 90})
        assert cadence == Cadence(kind="interval", every_minutes=90)
        assert cadence.to_dict() == {"kind": "interval", "everyMinutes": 90}

    def test_daily_requires_hour_and_minute(self) -> None:
        cadence = parse_cadence({"kind": "daily", "hour": 2, "minute": 30})
        assert cadence.to_dict() == {"kind": "daily", "hour": 2, "minute": 30}

    def test_weekly_carries_weekday(self) -> None:
        cadence = parse_cadence({"kind": "weekly", "hour": 3, "minute": 0, "weekday": 6})
        assert cadence.weekday == 6

    def test_monthly_carries_day_of_month(self) -> None:
        cadence = parse_cadence({"kind": "monthly", "hour": 4, "minute": 5, "dayOfMonth": 15})
        assert cadence.day_of_month == 15

    @pytest.mark.parametrize(
        "payload",
        [
            {"kind": "hourly"},
            {"kind": "interval", "everyMinutes": 0},
            {"kind": "interval", "everyMinutes": -5},
            {"kind": "daily", "hour": 24, "minute": 0},
            {"kind": "daily", "hour": 1, "minute": 60},
            {"kind": "weekly", "hour": 1, "minute": 0, "weekday": 7},
            {"kind": "monthly", "hour": 1, "minute": 0, "dayOfMonth": 0},
            {"kind": "monthly", "hour": 1, "minute": 0, "dayOfMonth": 32},
        ],
    )
    def test_invalid_payloads_are_rejected(self, payload: dict[str, object]) -> None:
        with pytest.raises(CadenceError):
            parse_cadence(payload)

    def test_interval_floor_blocks_a_hot_loop(self) -> None:
        """A one-minute cadence would re-enqueue a corpus sweep before it drains."""
        with pytest.raises(CadenceError):
            parse_cadence({"kind": "interval", "everyMinutes": 4})


class TestNextRunAt:
    def test_interval_advances_by_the_period(self) -> None:
        cadence = parse_cadence({"kind": "interval", "everyMinutes": 60})
        assert next_run_at(cadence, _at(2026, 7, 27, 10, 15)) == _at(2026, 7, 27, 11, 15)

    def test_daily_fires_later_the_same_day(self) -> None:
        cadence = parse_cadence({"kind": "daily", "hour": 2, "minute": 0})
        assert next_run_at(cadence, _at(2026, 7, 27, 1, 0)) == _at(2026, 7, 27, 2, 0)

    def test_daily_rolls_to_tomorrow_once_past(self) -> None:
        cadence = parse_cadence({"kind": "daily", "hour": 2, "minute": 0})
        assert next_run_at(cadence, _at(2026, 7, 27, 3, 0)) == _at(2026, 7, 28, 2, 0)

    def test_boundary_is_strict_so_a_schedule_never_refires_in_place(self) -> None:
        """At exactly the fire time the answer is the NEXT one, not the same instant."""
        cadence = parse_cadence({"kind": "daily", "hour": 2, "minute": 0})
        assert next_run_at(cadence, _at(2026, 7, 27, 2, 0)) == _at(2026, 7, 28, 2, 0)

    def test_weekly_finds_the_next_matching_weekday(self) -> None:
        # 2026-07-27 is a Monday (weekday 0); ask for Sunday (weekday 6).
        cadence = parse_cadence({"kind": "weekly", "hour": 3, "minute": 0, "weekday": 6})
        assert next_run_at(cadence, _at(2026, 7, 27, 12, 0)) == _at(2026, 8, 2, 3, 0)

    def test_weekly_same_day_before_the_hour_stays_today(self) -> None:
        cadence = parse_cadence({"kind": "weekly", "hour": 23, "minute": 0, "weekday": 0})
        assert next_run_at(cadence, _at(2026, 7, 27, 12, 0)) == _at(2026, 7, 27, 23, 0)

    def test_monthly_rolls_into_the_next_month(self) -> None:
        cadence = parse_cadence({"kind": "monthly", "hour": 1, "minute": 0, "dayOfMonth": 5})
        assert next_run_at(cadence, _at(2026, 7, 27, 0, 0)) == _at(2026, 8, 5, 1, 0)

    def test_monthly_skips_months_without_that_day(self) -> None:
        """Day 31 must not silently become Feb 28 — it skips to a month that has a 31st."""
        cadence = parse_cadence({"kind": "monthly", "hour": 1, "minute": 0, "dayOfMonth": 31})
        assert next_run_at(cadence, _at(2026, 1, 31, 2, 0)) == _at(2026, 3, 31, 1, 0)

    def test_monthly_crosses_the_year_boundary(self) -> None:
        cadence = parse_cadence({"kind": "monthly", "hour": 1, "minute": 0, "dayOfMonth": 5})
        assert next_run_at(cadence, _at(2026, 12, 10, 0, 0)) == _at(2027, 1, 5, 1, 0)

    def test_naive_input_is_rejected_rather_than_assumed_utc(self) -> None:
        cadence = parse_cadence({"kind": "daily", "hour": 2, "minute": 0})
        with pytest.raises(CadenceError):
            next_run_at(cadence, datetime(2026, 7, 27, 1, 0))  # noqa: DTZ001 — the point

    def test_non_utc_input_is_normalized_not_misread(self) -> None:
        from datetime import timedelta, timezone

        cadence = parse_cadence({"kind": "daily", "hour": 2, "minute": 0})
        # 03:00 at UTC+3 is 00:00 UTC, so the next 02:00 UTC is the same calendar day.
        moment = datetime(2026, 7, 27, 3, 0, tzinfo=timezone(timedelta(hours=3)))
        assert next_run_at(cadence, moment) == _at(2026, 7, 27, 2, 0)
