"""Liveness and readiness probes for the MCP service (Ops Phase 3, #18).

Mirrors the GraphQL API's health surface (``entrypoints/api/health.py`` +
``/health``/``/readyz`` in ``api/app.py``) so an operator has one mental model
across both entrypoints — same paths, same response shape, same status codes.

* **Liveness** (``/health``): the process is up. O(1), dependency-free, and
  information-free. It must NEVER call the network — a liveness probe that fails
  when the upstream is down makes the orchestrator kill a healthy process (FR-1).
* **Readiness** (``/readyz``): the service can actually serve — configuration
  loaded and the GraphQL upstream reachable. ``200`` when ready, ``503`` naming
  the failed check otherwise (FR-2). A negative result is cached briefly so a
  readiness storm cannot amplify an upstream outage.

Both paths are unauthenticated (the container runtime holds no token) and leak
nothing: no configuration, no upstream host, no stack trace — only a check name
and a coarse error kind. ``BearerAuthMiddleware`` and the rate limiter both exempt
:data:`PUBLIC_PATHS`.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from doc_ray import __version__
from doc_ray.entrypoints.api.health import readiness as _assemble_readiness

#: Paths served without authentication and never rate-limited. Both the auth
#: middleware and the rate limiter consult this, so the exemption is defined once.
PUBLIC_PATHS = frozenset({"/health", "/readyz"})

#: Readiness upstream probe timeout — short, so a slow upstream cannot stall the
#: probe (FR-2). The probe is a cheap unauthenticated GET, not a GraphQL query.
_READYZ_TIMEOUT_SECONDS = 2.0

#: How long a *negative* readiness result is cached (Open decision 3), so a burst
#: of readiness checks against a down upstream makes one probe, not one each.
_NEGATIVE_CACHE_SECONDS = 5.0

_OK = "ok"

#: ClientFactory is httpx.AsyncClient in production; injected in tests.
ClientFactory = Callable[..., Any]

#: Module-level negative-result cache: (expires_at_monotonic, report).
_neg_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def reset_readiness_cache() -> None:
    """Clear the negative readiness cache (tests + local diagnostics)."""
    _neg_cache.clear()


def liveness() -> dict[str, str]:
    """The liveness body: the process is up. No dependencies, no network (FR-1).

    ``version`` is the same value the MCP protocol handshake already advertises to
    every client, so it discloses nothing new — and it matches the API's ``/health``.
    """
    return {"status": _OK, "version": __version__}


def _upstream_health_url(api_url: str) -> str:
    """Derive the API's liveness URL from the GraphQL endpoint URL."""
    base = api_url.strip()
    if base.endswith("/graphql"):
        base = base[: -len("/graphql")]
    return base.rstrip("/") + "/health"


async def _probe_upstream(
    api_url: str, *, timeout: float, client_factory: ClientFactory | None
) -> str:
    """Return ``"ok"`` if the GraphQL upstream answers, else a host-free error kind.

    Deliberately information-free: the returned string carries only the exception
    class name (or the HTTP status), never the URL, host, or exception message —
    this feeds an unauthenticated probe body.
    """
    if client_factory is None:
        import httpx

        client_factory = httpx.AsyncClient
    url = _upstream_health_url(api_url)
    try:
        async with client_factory(timeout=timeout) as client:
            resp = await client.get(url)
    except Exception as exc:  # any failure is a readiness signal, never a crash
        return f"error: {type(exc).__name__}"
    status = int(getattr(resp, "status_code", 0))
    if status >= 500:
        return f"error: upstream HTTP {status}"
    return _OK


async def readiness_report(
    api_url: str | None,
    *,
    client_factory: ClientFactory | None = None,
    timeout: float = _READYZ_TIMEOUT_SECONDS,
    now: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], bool]:
    """Assemble the readiness report and whether the service is ready.

    Checks: ``config`` (the API URL is set) and ``api_upstream`` (it answers).
    A negative result is cached for :data:`_NEGATIVE_CACHE_SECONDS`.
    """
    cached = _neg_cache.get("readyz")
    if cached is not None and now() < cached[0]:
        return cached[1], False

    config_check = _OK if api_url else "error: DOCRAY_API_URL is not configured"
    if api_url:
        api_check = await _probe_upstream(api_url, timeout=timeout, client_factory=client_factory)
    else:
        api_check = "error: not checked (no upstream configured)"

    report = _assemble_readiness({"config": config_check, "api_upstream": api_check})
    report["version"] = __version__
    ready = bool(report["ready"])
    if not ready:
        _neg_cache["readyz"] = (now() + _NEGATIVE_CACHE_SECONDS, report)
    else:
        _neg_cache.pop("readyz", None)
    return report, ready


async def health_route(request: Request) -> JSONResponse:
    """``GET /health`` — liveness. Always ``200`` while the process runs (FR-1)."""
    return JSONResponse(liveness())


async def readyz_route(request: Request) -> JSONResponse:
    """``GET /readyz`` — readiness. ``200`` when serveable, else ``503`` (FR-2)."""
    report, ready = await readiness_report(os.environ.get("DOCRAY_API_URL"))
    return JSONResponse(report, status_code=200 if ready else 503)


#: Mounted by ``server.create_app`` before the auth/limiter middleware exempts them.
ROUTES: tuple[tuple[str, Callable[[Request], Awaitable[JSONResponse]]], ...] = (
    ("/health", health_route),
    ("/readyz", readyz_route),
)
