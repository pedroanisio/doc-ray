"""Per-principal request rate limiting for the MCP service (Ops Phase 3, #18).

A thin Starlette middleware over the shared :mod:`doc_ray.domain.rate_limit`
fixed-window limiter (the same core the webapp uses — one implementation, not
two, per Open decision 1). Keyed per principal (a non-reversible fingerprint of
the bearer token the auth middleware resolved), falling back to the client IP for
unauthenticated callers. On breach it returns ``429`` with a ``Retry-After`` header
and logs the rejection by fingerprint — never the token (FR-5).

State is in-process (Open decision 2): correct for the single MCP container in
production. With N replicas the effective limit is N× — documented in
``docs/mcp-service.md``. The health probes (:data:`health.PUBLIC_PATHS`) are never
rate-limited: throttling a liveness probe is a self-inflicted outage (FR-4).
"""

from __future__ import annotations

import hashlib
import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from doc_ray.domain.rate_limit import FixedWindowRateLimiter, RateLimitRule
from doc_ray.entrypoints.mcp import tokens
from doc_ray.entrypoints.mcp.health import PUBLIC_PATHS

logger = logging.getLogger("doc_ray.mcp.ratelimit")

#: Single logical route for the MCP surface; the limiter keys on (route, client).
_ROUTE = "mcp"


def _client_key(request: Request) -> str:
    """A stable, non-reversible caller key: token fingerprint, else client IP.

    Mirrors ``audit._caller``: the bearer token (set by ``BearerAuthMiddleware``)
    is hashed to 12 hex chars — never stored or logged in the clear. Anonymous
    callers (which in practice only reach the exempt probes) fall back to IP.
    """
    token = tokens.current_token()
    if token:
        return "principal:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-principal limiter; exempts the health probes."""

    def __init__(
        self, app: ASGIApp, *, limiter: FixedWindowRateLimiter, rule: RateLimitRule
    ) -> None:
        """Capture the shared limiter instance and the configured window rule."""
        super().__init__(app)
        self._limiter = limiter
        self._rule = rule

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Throttle non-probe requests per principal; 429 + Retry-After on breach."""
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        client_id = _client_key(request)
        result = self._limiter.check(_ROUTE, client_id, self._rule)
        if not result.allowed:
            # FR-5: observable, but the key is a fingerprint — no token material.
            logger.warning(
                "rate_limit rejected client=%s limit=%d/%ds retry_after=%ds",
                client_id,
                self._rule.requests,
                self._rule.window_seconds,
                result.retry_after_seconds,
            )
            return JSONResponse(
                {"error": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(result.retry_after_seconds)},
            )
        return await call_next(request)
