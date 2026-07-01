"""Minimal, dependency-free ULID (Universally Unique Lexicographically
Sortable Identifier) implementation.

A ULID is 128 bits = 48-bit millisecond timestamp + 80-bit randomness,
rendered as 26 Crockford base32 characters. Because the timestamp is the
most-significant part, ASCII/byte sort order equals chronological order —
which is exactly why sentences are keyed by ULID rather than random UUIDv4.

Spec: https://github.com/ulid/spec
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

# Crockford base32 (excludes I, L, O, U to avoid ambiguity).
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}

_TS_BITS = 48
_RAND_BITS = 80
_MAX_TS = (1 << _TS_BITS) - 1
_MAX_RAND = (1 << _RAND_BITS) - 1
_TS_LEN = 10          # base32 chars for the timestamp
_RAND_LEN = 16        # base32 chars for the randomness

# Regex the Postgres `ulid` domain also enforces; kept here so callers/tests
# can validate without a DB round-trip.
ULID_REGEX = r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$"


def _b32_encode(value: int, length: int) -> str:
    """Big-endian Crockford base32 of `value` in exactly `length` chars."""
    out = [""] * length
    for idx in range(length - 1, -1, -1):
        out[idx] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(out)


def _b32_decode(text: str) -> int:
    value = 0
    for ch in text:
        value = (value << 5) | _DECODE[ch.upper()]
    return value


@dataclass(frozen=True, order=True)
class ULID:
    """An immutable ULID value. Ordered by (timestamp, randomness)."""

    timestamp_ms: int
    randomness: int

    def __post_init__(self) -> None:
        if not (0 <= self.timestamp_ms <= _MAX_TS):
            raise ValueError(f"timestamp out of range: {self.timestamp_ms}")
        if not (0 <= self.randomness <= _MAX_RAND):
            raise ValueError(f"randomness out of range: {self.randomness}")

    def __str__(self) -> str:
        return _b32_encode(self.timestamp_ms, _TS_LEN) + _b32_encode(
            self.randomness, _RAND_LEN
        )

    @classmethod
    def parse(cls, text: str) -> "ULID":
        if len(text) != _TS_LEN + _RAND_LEN:
            raise ValueError(f"ULID must be 26 chars, got {len(text)}")
        return cls(_b32_decode(text[:_TS_LEN]), _b32_decode(text[_TS_LEN:]))

    @property
    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp_ms / 1000, tz=timezone.utc)


class ULIDFactory:
    """Thread-safe, monotonic ULID generator.

    Within the same millisecond, randomness is incremented (not re-rolled) so
    generation order is preserved in the string form. `clock`/`rng` are
    injectable for deterministic testing.
    """

    def __init__(self, clock=None, rng=None):
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._rng = rng or (lambda: int.from_bytes(os.urandom(10), "big"))
        self._lock = threading.Lock()
        self._last_ts: int | None = None
        self._last_rand: int = 0

    def new(self) -> ULID:
        with self._lock:
            ts = self._clock()
            if self._last_ts is not None and ts <= self._last_ts:
                ts = self._last_ts
                rand = self._last_rand + 1
                if rand > _MAX_RAND:      # exhausted this ms; roll into the next
                    ts += 1
                    rand = self._rng() & _MAX_RAND
            else:
                rand = self._rng() & _MAX_RAND
            self._last_ts = ts
            self._last_rand = rand
            return ULID(ts, rand)


# Process-wide default factory + convenience helpers.
_default = ULIDFactory()


def new_ulid() -> ULID:
    return _default.new()


def new_ulid_str() -> str:
    return str(_default.new())
