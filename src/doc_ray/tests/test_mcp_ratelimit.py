"""MCP Ops Phase 3 (#18): per-principal request rate limiting.

The limiter is the pure ``domain.rate_limit`` core (shared with the webapp); this
covers the MCP middleware wrapped around it: the boundary behaviour, the
probe-exemption rule (a limiter that throttles ``/health`` is a self-inflicted
outage), and the FR-5 guarantee that a rejection is logged by principal
*fingerprint*, never the bearer token itself.
"""

from __future__ import annotations

import logging

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from doc_ray.domain.rate_limit import FixedWindowRateLimiter, RateLimitRule
from doc_ray.entrypoints.mcp import ratelimit, tokens
from doc_ray.tests._asgi import asgi_client


async def _ok(request):
    return PlainTextResponse("ok")


def _app(rule: RateLimitRule) -> Starlette:
    app = Starlette(routes=[Route("/x", _ok), Route("/health", _ok), Route("/readyz", _ok)])
    app.add_middleware(ratelimit.RateLimitMiddleware, limiter=FixedWindowRateLimiter(), rule=rule)
    return app


# --------------------------------------------------------------------------
# 1. Boundary — allow up to the limit, then 429 + Retry-After.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requests_are_allowed_up_to_the_limit_then_rejected_with_retry_after():
    async with asgi_client(_app(RateLimitRule(requests=2, window_seconds=60))) as client:
        assert (await client.get("/x")).status_code == 200
        assert (await client.get("/x")).status_code == 200
        blocked = await client.get("/x")
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_a_disabled_rule_never_throttles():
    async with asgi_client(_app(RateLimitRule(requests=0, window_seconds=0))) as client:
        for _ in range(10):
            assert (await client.get("/x")).status_code == 200


# --------------------------------------------------------------------------
# 2. Probe exemption — /health and /readyz are never rate-limited (FR-4).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_and_readyz_are_never_rate_limited():
    async with asgi_client(_app(RateLimitRule(requests=1, window_seconds=60))) as client:
        for _ in range(20):
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/readyz")).status_code == 200


# --------------------------------------------------------------------------
# 3. Client key — a principal fingerprint, never the token (FR-5).
# --------------------------------------------------------------------------


def test_client_key_is_a_fingerprint_of_the_token_never_the_token():
    reset = tokens.caller_token.set("super-secret-bearer-abcdef")
    try:
        key = ratelimit._client_key(_FakeRequest(host="1.2.3.4"))
    finally:
        tokens.caller_token.reset(reset)
    assert key.startswith("principal:")
    assert "super-secret-bearer-abcdef" not in key
    assert "1.2.3.4" not in key  # the token wins over the IP when present


def test_client_key_falls_back_to_ip_when_anonymous():
    reset = tokens.caller_token.set("")
    try:
        key = ratelimit._client_key(_FakeRequest(host="9.9.9.9"))
    finally:
        tokens.caller_token.reset(reset)
    assert key == "ip:9.9.9.9"


@pytest.mark.asyncio
async def test_rejection_logs_the_fingerprint_and_never_the_token(caplog):
    token = "secret-token-do-not-log"
    reset = tokens.caller_token.set(token)
    try:
        with caplog.at_level(logging.WARNING, logger="doc_ray.mcp.ratelimit"):
            async with asgi_client(_app(RateLimitRule(requests=1, window_seconds=60))) as client:
                # first request opens the window from this principal; the fingerprint is
                # set per-request by the caller_token contextvar, so drive it twice.
                await client.get("/x")
                await client.get("/x")
    finally:
        tokens.caller_token.reset(reset)
    rejections = [r.getMessage() for r in caplog.records if r.name == "doc_ray.mcp.ratelimit"]
    assert rejections, "a rejection must be logged"
    assert all(token not in line for line in rejections)


class _FakeRequest:
    def __init__(self, host: str) -> None:
        self.client = type("C", (), {"host": host})()
        self.url = type("U", (), {"path": "/x"})()
