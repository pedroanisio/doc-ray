"""Backwards-compatible re-export: the limiter now lives in the domain layer.

The fixed-window limiter moved to :mod:`doc_ray.domain.rate_limit` (#18) so the
webapp and the MCP entrypoint share one implementation. This module re-exports it
unchanged, so ``from webapp.rate_limit import ...`` (and the top-level
``from rate_limit import ...`` used when ``webapp/`` is on ``sys.path``) keep working.
"""

from __future__ import annotations

from doc_ray.domain.rate_limit import (
    FixedWindowRateLimiter,
    RateLimitResult,
    RateLimitRule,
    rate_limit_rule_from_env,
)

__all__ = [
    "FixedWindowRateLimiter",
    "RateLimitResult",
    "RateLimitRule",
    "rate_limit_rule_from_env",
]
