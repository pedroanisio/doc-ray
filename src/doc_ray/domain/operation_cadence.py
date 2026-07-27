"""Recurrence math for scheduled operations — pure, total, and UTC-only.

WHY A BOUNDED CADENCE AND NOT CRON
----------------------------------
A full crontab expression (ranges, steps, lists, and the day-of-month/day-of-week
OR rule) is deceptively hard to implement correctly, and a subtly wrong parser
fails silently: the schedule simply never fires, or fires on the wrong day, and
nobody notices until an operator asks why a backfill went stale. This module
therefore models the four cadences an operations console actually needs —
interval, daily, weekly, monthly — as an explicit, validated structure. Every
recurrence is representable in the UI as a form rather than a string a human has
to get right, and every branch is pinned by a test.

All arithmetic is UTC. Callers passing a naive datetime get an error rather than
an implicit timezone, because "which midnight" is exactly the ambiguity that
turns an off-peak GPU sweep into a peak-hours one.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

#: Cadence discriminator values, stored verbatim in ``operation_schedule.cadence``.
CadenceKind = Literal["interval", "daily", "weekly", "monthly"]

CADENCE_KINDS: tuple[str, ...] = ("interval", "daily", "weekly", "monthly")

#: A corpus sweep enqueues hundreds of jobs and takes hours to drain. Re-firing it
#: every minute would stack sweeps on top of each other forever, so the floor is a
#: correctness guard rather than a preference.
MIN_INTERVAL_MINUTES = 5


class CadenceError(ValueError):
    """A cadence payload is not a recurrence this system can honour."""


@dataclass(frozen=True, slots=True)
class Cadence:
    """A validated recurrence rule. Construct through :func:`parse_cadence`."""

    kind: CadenceKind
    every_minutes: int | None = None
    hour: int | None = None
    minute: int | None = None
    weekday: int | None = None
    day_of_month: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the camelCase shape stored in JSONB and sent over GraphQL."""
        if self.kind == "interval":
            return {"kind": "interval", "everyMinutes": self.every_minutes}
        payload: dict[str, Any] = {"kind": self.kind, "hour": self.hour, "minute": self.minute}
        if self.kind == "weekly":
            payload["weekday"] = self.weekday
        if self.kind == "monthly":
            payload["dayOfMonth"] = self.day_of_month
        return payload

    def describe(self) -> str:
        """Human phrasing for the console and for audit entries."""
        if self.kind == "interval":
            return f"every {self.every_minutes} min"
        clock = f"{self.hour:02d}:{self.minute:02d} UTC"
        if self.kind == "daily":
            return f"daily at {clock}"
        if self.kind == "weekly":
            return f"{calendar.day_name[self.weekday or 0]}s at {clock}"
        return f"day {self.day_of_month} monthly at {clock}"


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    # bool is an int subclass; accepting it would let `true` mean hour 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise CadenceError(f"cadence field {key!r} must be an integer, got {value!r}")
    return value


def _require_range(value: int, low: int, high: int, key: str) -> int:
    if not low <= value <= high:
        raise CadenceError(f"cadence field {key!r} must be in [{low}, {high}], got {value}")
    return value


def _parse_clock(payload: dict[str, Any]) -> tuple[int, int]:
    hour = _require_range(_require_int(payload, "hour"), 0, 23, "hour")
    minute = _require_range(_require_int(payload, "minute"), 0, 59, "minute")
    return hour, minute


def parse_cadence(payload: dict[str, Any]) -> Cadence:
    """Validate an untrusted cadence payload into a :class:`Cadence`.

    Raises :class:`CadenceError` on anything this module cannot honour exactly.
    Rejecting is deliberate: a schedule that quietly degrades to a different
    recurrence than the operator asked for is worse than one that refuses to save.
    """
    kind = payload.get("kind")
    if kind not in CADENCE_KINDS:
        raise CadenceError(f"cadence kind must be one of {CADENCE_KINDS}, got {kind!r}")

    if kind == "interval":
        every = _require_int(payload, "everyMinutes")
        if every < MIN_INTERVAL_MINUTES:
            raise CadenceError(
                f"interval cadence must be at least {MIN_INTERVAL_MINUTES} minutes, got {every}"
            )
        return Cadence(kind="interval", every_minutes=every)

    hour, minute = _parse_clock(payload)
    if kind == "daily":
        return Cadence(kind="daily", hour=hour, minute=minute)
    if kind == "weekly":
        weekday = _require_range(_require_int(payload, "weekday"), 0, 6, "weekday")
        return Cadence(kind="weekly", hour=hour, minute=minute, weekday=weekday)
    day_of_month = _require_range(_require_int(payload, "dayOfMonth"), 1, 31, "dayOfMonth")
    return Cadence(kind="monthly", hour=hour, minute=minute, day_of_month=day_of_month)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise CadenceError("next_run_at requires an aware datetime; naive input is ambiguous")
    return moment.astimezone(UTC)


def _clock_on(day: datetime, cadence: Cadence) -> datetime:
    return day.replace(hour=cadence.hour or 0, minute=cadence.minute or 0, second=0, microsecond=0)


def _next_monthly(cadence: Cadence, after: datetime) -> datetime:
    """First matching day-of-month strictly after ``after``.

    Months without the requested day are SKIPPED, not clamped: an operator who
    asked for the 31st means the 31st, and silently firing on Feb 28 would be a
    different schedule than the one they saved.
    """
    year, month = after.year, after.month
    target = cadence.day_of_month or 1
    # 48 months is a hard bound: day 31 recurs at worst every other month, so any
    # valid cadence resolves far inside this window and a bug cannot spin forever.
    for _ in range(48):
        if target <= calendar.monthrange(year, month)[1]:
            candidate = _clock_on(datetime(year, month, target, tzinfo=UTC), cadence)
            if candidate > after:
                return candidate
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    raise CadenceError(f"no occurrence found for cadence {cadence.describe()}")


def next_run_at(cadence: Cadence, after: datetime) -> datetime:
    """Return the first firing time strictly after ``after`` (UTC).

    Strictness matters: the scheduler calls this with the time it just fired, so
    a non-strict boundary would re-arm the schedule at the same instant and spin.
    """
    moment = _as_utc(after)

    if cadence.kind == "interval":
        return moment + timedelta(minutes=cadence.every_minutes or MIN_INTERVAL_MINUTES)

    if cadence.kind == "daily":
        candidate = _clock_on(moment, cadence)
        return candidate if candidate > moment else candidate + timedelta(days=1)

    if cadence.kind == "weekly":
        candidate = _clock_on(moment, cadence)
        delta = ((cadence.weekday or 0) - candidate.weekday()) % 7
        candidate += timedelta(days=delta)
        return candidate if candidate > moment else candidate + timedelta(days=7)

    return _next_monthly(cadence, moment)
