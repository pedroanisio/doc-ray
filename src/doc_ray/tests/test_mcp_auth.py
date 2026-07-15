"""BearerAuthMiddleware validates the caller's token exactly like the GraphQL API, no network."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

import doc_ray.entrypoints.mcp.auth as authmod
from doc_ray.entrypoints.mcp.auth import BearerAuthMiddleware
from doc_ray.entrypoints.mcp.corpus import caller_token


def _cfg(*, tokens=None, allow_anon=True, jwt_secret=None):
    return SimpleNamespace(
        api_tokens=tokens or {},
        api_allow_anonymous=allow_anon,
        api_jwt_secret=jwt_secret,
        api_jwt_issuer="doc-ray",
        api_jwt_audience=None,
    )


def _req(headers):
    return SimpleNamespace(headers=Headers(headers))


@pytest.fixture(autouse=True)
def _reset_token():
    caller_token.set("")
    yield


@pytest.mark.asyncio
async def test_valid_static_token_passes_and_sets_then_resets_contextvar():
    seen = {}
    mw = BearerAuthMiddleware(app=None, cfg=_cfg(tokens={"tok-1": ("pid", frozenset({"read"}))}))

    async def call_next(_request):
        seen["token"] = caller_token.get()
        return "OK"

    assert await mw.dispatch(_req({"authorization": "Bearer tok-1"}), call_next) == "OK"
    assert seen["token"] == "tok-1"
    assert caller_token.get() == ""  # reset after the request


@pytest.mark.asyncio
async def test_x_docray_api_token_header_accepted():
    mw = BearerAuthMiddleware(app=None, cfg=_cfg(tokens={"tok-x": ("pid", frozenset({"read"}))}))

    async def call_next(_request):
        return caller_token.get()

    assert await mw.dispatch(_req({"x-docray-api-token": "tok-x"}), call_next) == "tok-x"


@pytest.mark.asyncio
async def test_unknown_token_is_401():
    mw = BearerAuthMiddleware(app=None, cfg=_cfg(tokens={"tok-1": ("pid", frozenset({"read"}))}))

    async def call_next(_request):
        raise AssertionError("handler must not run for an invalid token")

    resp = await mw.dispatch(_req({"authorization": "Bearer nope"}), call_next)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_no_token_passes_when_anonymous_allowed():
    mw = BearerAuthMiddleware(app=None, cfg=_cfg(allow_anon=True))

    async def call_next(_request):
        assert caller_token.get() == ""  # anonymous -> empty token forwarded
        return "OK"

    assert await mw.dispatch(_req({}), call_next) == "OK"


@pytest.mark.asyncio
async def test_no_token_is_401_when_anonymous_disabled():
    mw = BearerAuthMiddleware(app=None, cfg=_cfg(allow_anon=False))

    async def call_next(_request):
        raise AssertionError

    resp = await mw.dispatch(_req({}), call_next)
    assert resp.status_code == 401
    # A Bearer challenge; it also carries RFC 9728 resource_metadata when the
    # public base URL is configured (the OAuth discovery entry point).
    assert resp.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.asyncio
async def test_jwt_token_is_validated_via_verifier(monkeypatch):
    seen = []
    monkeypatch.setattr(authmod, "verify_access_jwt", lambda tok, **kw: seen.append(tok))
    mw = BearerAuthMiddleware(app=None, cfg=_cfg(jwt_secret="s"))

    async def call_next(_request):
        return caller_token.get()

    assert await mw.dispatch(_req({"authorization": "Bearer a.b.c"}), call_next) == "a.b.c"
    assert seen == ["a.b.c"]


@pytest.mark.asyncio
async def test_invalid_jwt_is_401(monkeypatch):
    def _boom(_tok, **_kw):
        raise ValueError("bad")

    monkeypatch.setattr(authmod, "verify_access_jwt", _boom)
    mw = BearerAuthMiddleware(app=None, cfg=_cfg(jwt_secret="s"))

    async def call_next(_request):
        raise AssertionError

    resp = await mw.dispatch(_req({"authorization": "Bearer a.b.c"}), call_next)
    assert resp.status_code == 401
