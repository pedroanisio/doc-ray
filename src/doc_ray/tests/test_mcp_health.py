"""MCP Ops Phase 3 (#18): liveness/readiness probes.

Liveness must be O(1), unauthenticated, dependency-free, and information-free.
Readiness may touch the network (the GraphQL upstream) but must degrade to a 503
that names the failed check without leaking the upstream host or a stack trace.
Both probes must bypass ``BearerAuthMiddleware`` — an ordering guarantee proven
here through the fully assembled ASGI app, unauthenticated.
"""

from __future__ import annotations

import dataclasses

import pytest
from starlette.testclient import TestClient

from doc_ray import __version__
from doc_ray.config import Config
from doc_ray.entrypoints.mcp import health, server


class _FakeResp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    """An httpx.AsyncClient stand-in: returns a canned status, or raises on connect."""

    def __init__(self, *, status: int | None = None, error: Exception | None = None) -> None:
        self._status = status
        self._error = error
        self.gets: list[str] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, url: str) -> _FakeResp:
        self.gets.append(url)
        if self._error is not None:
            raise self._error
        assert self._status is not None
        return _FakeResp(self._status)


def _up(**kw):
    return lambda **_: _FakeClient(status=200, **kw)


def _down(exc=None):
    err = exc or ConnectionError("refused")
    return lambda **_: _FakeClient(error=err)


@pytest.fixture(autouse=True)
def _clear_cache():
    health.reset_readiness_cache()
    yield
    health.reset_readiness_cache()


# --------------------------------------------------------------------------
# 1. Liveness — pure, information-free.
# --------------------------------------------------------------------------


def test_liveness_is_status_ok_and_only_the_already_advertised_version():
    body = health.liveness()
    assert body == {"status": "ok", "version": __version__}


# --------------------------------------------------------------------------
# 2. Readiness — both directions, honest about which check failed.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readiness_is_ready_when_config_loads_and_upstream_answers():
    report, ready = await health.readiness_report("http://api:8000/graphql", client_factory=_up())
    assert ready is True
    assert report["ready"] is True
    assert report["checks"]["config"] == "ok"
    assert report["checks"]["api_upstream"] == "ok"


@pytest.mark.asyncio
async def test_readiness_is_not_ready_and_names_the_upstream_when_it_is_down():
    report, ready = await health.readiness_report("http://api:8000/graphql", client_factory=_down())
    assert ready is False
    assert report["checks"]["api_upstream"].startswith("error")


@pytest.mark.asyncio
async def test_readiness_fails_when_the_api_url_is_not_configured():
    report, ready = await health.readiness_report(None, client_factory=_up())
    assert ready is False
    assert report["checks"]["config"].startswith("error")


@pytest.mark.asyncio
async def test_readiness_leaks_no_upstream_host_or_stack_trace_on_failure():
    # Security NFR: an unauthenticated probe must not disclose the upstream URL,
    # host, or an exception message/traceback — only a check name + an error kind.
    boom = ConnectionError("could not connect to api.internal:8000 secret-detail")
    report, ready = await health.readiness_report(
        "http://api.internal:8000/graphql", client_factory=_down(boom)
    )
    serialized = str(report)
    assert "api.internal" not in serialized
    assert "secret-detail" not in serialized
    assert "8000" not in serialized


# --------------------------------------------------------------------------
# 3. Negative-result caching — a readiness storm cannot amplify an outage.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_negative_readiness_result_is_cached_briefly():
    # now() reads: miss caches at t=1000 (expiry 1005); a check at t=1000.5 is
    # inside the window (served from cache, no probe); a check at t=1010 is past it
    # (probes again, then re-caches — two reads). Cache window is 5s.
    clock = iter([1000.0, 1000.5, 1010.0, 1010.0])
    factory = _down()
    calls = {"n": 0}

    def counting_factory(**kw):
        calls["n"] += 1
        return factory(**kw)

    now = lambda: next(clock)  # noqa: E731
    await health.readiness_report(
        "http://api:8000/graphql", client_factory=counting_factory, now=now
    )
    await health.readiness_report(
        "http://api:8000/graphql", client_factory=counting_factory, now=now
    )
    assert calls["n"] == 1  # second call within the window served from cache
    # after the window, it probes again
    await health.readiness_report(
        "http://api:8000/graphql", client_factory=counting_factory, now=now
    )
    assert calls["n"] == 2


# --------------------------------------------------------------------------
# 4. Wiring — the probes bypass BearerAuthMiddleware (FR-3), the MCP path does not.
# --------------------------------------------------------------------------


def _fail_closed_cfg() -> Config:
    return dataclasses.replace(
        Config.from_env(), api_allow_anonymous=False, api_tokens={"secret-tok": frozenset({"read"})}
    )


def test_health_is_reachable_unauthenticated_even_though_the_mcp_path_is_not(monkeypatch):
    # FR-1/FR-3: /health returns 200 with no Authorization header AND makes no
    # network call — patch httpx so any use would raise, proving liveness is
    # dependency-free even while the upstream is unreachable.
    import httpx

    def _explode(**_):
        raise AssertionError("liveness must not touch the network")

    monkeypatch.setattr(httpx, "AsyncClient", _explode)
    with TestClient(server.create_app(_fail_closed_cfg())) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        # the protected MCP path still demands auth — the middleware IS active
        assert client.get("/mcp").status_code == 401


def test_readyz_is_reachable_unauthenticated_and_503s_when_upstream_is_down(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _down())
    with TestClient(server.create_app(_fail_closed_cfg())) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["ready"] is False
        assert resp.json()["checks"]["api_upstream"].startswith("error")


def test_public_paths_names_both_probes():
    assert frozenset({"/health", "/readyz"}) == health.PUBLIC_PATHS
