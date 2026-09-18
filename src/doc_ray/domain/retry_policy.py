"""Decide how long a failed job waits before its next attempt.

A retry schedule encodes a belief about why the work failed. One schedule for
every exception encodes the belief that all failures are alike, and they are not:

* A **deterministic** failure — a missing table, a bad keyword argument, a 400 —
  will fail identically on every attempt. Retrying is nearly free and nearly
  pointless, so the delay stays short and the attempt budget burns fast.
* A **contention** failure — an exhausted GPU, a refused connection to a model
  endpoint, a 429 — says a *shared* resource was busy. The work is fine. Retrying
  seconds later merely re-enters the same queue behind the same load.

Treating the second as the first is what turns a transient burst into permanent
data loss: dozens of annotate jobs hit ``CUDA out of memory`` on a saturated GPU
and exhausted all three attempts within fifteen seconds, because the schedule
was 5s then 10s, while the GPU stayed saturated for the better part of an hour.
Because ``annotate`` heads the pipeline chain, each terminal failure also left
its document with no downstream jobs — no enrichment, no embedding, no visual
tier — and nothing re-enqueues a failed job, so they simply stopped.

The classification is deliberately conservative: anything not recognised as
contention is treated as deterministic. Misreading a real bug as contention
would hide it behind an hour-long delay, which is the worse error.
"""

from __future__ import annotations

from enum import Enum

#: Deterministic failures: 5s, 10s, 20s ... capped. Cheap to retry, unlikely to help.
DETERMINISTIC_BASE_SECONDS = 5.0
DETERMINISTIC_CAP_SECONDS = 300.0

#: Contention failures: 30min, 60min ... capped at 4h. Sized against the real
#: incident rather than a round number -- two waits must outlast the ~44-minute
#: saturation burst that exhausted the budget, or the schedule has not fixed
#: anything it claims to.
CONTENTION_BASE_SECONDS = 1800.0
CONTENTION_CAP_SECONDS = 14400.0


class FailureClass(str, Enum):
    """Why a job failed, to the extent the exception reveals it."""

    DETERMINISTIC = "deterministic"
    CONTENTION = "contention"


#: Exception type names that mean a shared resource ran out. Matched by name so
#: this module never imports torch (a worker-only, multi-gigabyte dependency)
#: just to recognise its error.
_CONTENTION_TYPE_NAMES = frozenset(
    {
        "OutOfMemoryError",
        "MemoryError",
        "CudaOutOfMemoryError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "TimeoutError",
        "ServerSelectionTimeoutError",
        "PoolTimeout",
        "OperationalError",
    }
)

#: Substrings that identify contention when the type alone does not. Lowercased
#: before matching.
_CONTENTION_MARKERS = (
    "out of memory",
    "connection refused",
    "connection reset",
    "too many requests",
    "service unavailable",
    "resource temporarily unavailable",
    "no space left on device",
    "cannot allocate memory",
    "server is busy",
    "try again later",
)

#: HTTP statuses that mean "busy, come back later" rather than "your request is wrong".
_CONTENTION_HTTP_STATUS = frozenset({408, 429, 502, 503, 504})


def _status_of(exc: BaseException) -> int | None:
    """Return an HTTP-ish status code carried by *exc*, if any."""
    for attribute in ("status", "code", "status_code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    return None


def _causes(exc: BaseException) -> list[BaseException]:
    """Return *exc* and the chain it wraps.

    ``URLError(ConnectionRefusedError(...))`` is the shape the caption lane's 905
    failures arrived in: the useful signal is one level down, in ``reason``.
    """
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(seen) < 8:
        seen.append(current)
        nested = getattr(current, "reason", None)
        following = nested if isinstance(nested, BaseException) else None
        current = following or current.__cause__ or current.__context__
        if current in seen:
            break
    return seen


def classify(exc: BaseException) -> FailureClass:
    """Classify *exc* as contention or deterministic.

    Unrecognised exceptions are deterministic on purpose — see the module note on
    why the conservative direction is the safe one.
    """
    for error in _causes(exc):
        name = type(error).__name__
        if name in _CONTENTION_TYPE_NAMES:
            # An HTTP error can surface under a contention-looking type while
            # carrying a status that says the request itself was wrong.
            status = _status_of(error)
            if status is not None and 400 <= status < 500 and status not in _CONTENTION_HTTP_STATUS:
                continue
            return FailureClass.CONTENTION
        status = _status_of(error)
        if status in _CONTENTION_HTTP_STATUS:
            return FailureClass.CONTENTION
        text = f"{name}: {error}".lower()
        if any(marker in text for marker in _CONTENTION_MARKERS):
            return FailureClass.CONTENTION
    return FailureClass.DETERMINISTIC


def backoff_seconds(attempts: int, failure_class: FailureClass) -> float:
    """Return the delay before the attempt following *attempts* failures."""
    if failure_class is FailureClass.CONTENTION:
        base, cap = CONTENTION_BASE_SECONDS, CONTENTION_CAP_SECONDS
    else:
        base, cap = DETERMINISTIC_BASE_SECONDS, DETERMINISTIC_CAP_SECONDS
    return float(min(base * 2 ** max(0, attempts - 1), cap))
