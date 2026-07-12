"""Small fixed-window rate limiter for expensive webapp routes."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitRule:
    """A fixed-window limit; non-positive values disable throttling."""

    requests: int
    window_seconds: int

    @property
    def enabled(self) -> bool:
        """Return whether this rule should throttle requests."""
        return self.requests > 0 and self.window_seconds > 0


@dataclass(frozen=True)
class RateLimitResult:
    """The outcome of one rate-limit check."""

    allowed: bool
    retry_after_seconds: int = 0


class FixedWindowRateLimiter:
    """In-memory fixed-window limiter keyed by route and client id."""

    def __init__(self, *, clock=time.monotonic):
        """Initialize the limiter with an injectable monotonic clock."""
        self._clock = clock
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}

    def check(self, route: str, client_id: str, rule: RateLimitRule) -> RateLimitResult:
        """Record one request and return whether it is allowed."""
        if not rule.enabled:
            return RateLimitResult(allowed=True)

        now = self._clock()
        key = (route, client_id)
        window_started_at, count = self._windows.get(key, (now, 0))
        elapsed = now - window_started_at
        if elapsed >= rule.window_seconds:
            window_started_at = now
            count = 0
            elapsed = 0

        if count >= rule.requests:
            retry_after = max(1, math.ceil(rule.window_seconds - elapsed))
            return RateLimitResult(allowed=False, retry_after_seconds=retry_after)

        self._windows[key] = (window_started_at, count + 1)
        return RateLimitResult(allowed=True)

    def reset(self) -> None:
        """Clear all counters."""
        self._windows.clear()


def rate_limit_rule_from_env(
    env: Mapping[str, str],
    prefix: str,
    *,
    default_requests: int,
    default_window_seconds: int,
) -> RateLimitRule:
    """Build a rule from ``<prefix>_REQUESTS`` and ``<prefix>_WINDOW_SECONDS``."""
    return RateLimitRule(
        requests=_env_int(env, f"{prefix}_REQUESTS", default_requests),
        window_seconds=_env_int(env, f"{prefix}_WINDOW_SECONDS", default_window_seconds),
    )


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default
