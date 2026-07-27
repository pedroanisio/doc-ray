"""MCP transport hot-path contracts: pooling, shutdown, and scoped gzip."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from doc_ray.entrypoints.mcp import corpus, http_client, server, tokens


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"data": {"version": "1.50.0"}}


class _Client:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.closed = 0
        self.headers: list[dict[str, str]] = []

    async def post(self, url, json, headers):
        self.headers.append(headers)
        return _Response()

    async def aclose(self) -> None:
        self.closed += 1


@pytest.fixture(autouse=True)
def _reset_shared_client():
    asyncio.run(http_client.close_client())
    tokens.caller_token.set("")
    yield
    tokens.caller_token.set("")
    asyncio.run(http_client.close_client())


@pytest.mark.asyncio
async def test_sequential_gql_calls_reuse_one_client_without_default_auth(monkeypatch):
    created = []

    def factory(**kwargs):
        client = _Client(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(http_client.httpx, "AsyncClient", factory)
    tokens.caller_token.set("alice-token")
    await corpus.gql("query { version }", {})
    tokens.caller_token.set("bob-token")
    await corpus.gql("query { version }", {})

    assert len(created) == 1
    assert "headers" not in created[0].kwargs
    assert "auth" not in created[0].kwargs
    assert "cookies" not in created[0].kwargs
    assert created[0].headers == [
        {"content-type": "application/json", "Authorization": "Bearer alice-token"},
        {"content-type": "application/json", "Authorization": "Bearer bob-token"},
    ]


@pytest.mark.asyncio
async def test_close_clears_cached_client_and_next_acquire_is_new(monkeypatch):
    created = []

    def factory(**kwargs):
        client = _Client(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(http_client.httpx, "AsyncClient", factory)
    first = await http_client.acquire_client()
    assert await http_client.acquire_client() is first
    await http_client.close_client()
    second = await http_client.acquire_client()

    assert second is not first
    assert created == [first, second]
    assert first.closed == 1


@pytest.mark.asyncio
async def test_concurrent_first_use_constructs_exactly_one_client(monkeypatch):
    created = []

    def factory(**kwargs):
        client = _Client(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(http_client.httpx, "AsyncClient", factory)
    clients = await asyncio.gather(*(http_client.acquire_client() for _ in range(20)))

    assert len(created) == 1
    assert all(client is created[0] for client in clients)


@pytest.mark.asyncio
async def test_pool_limits_are_explicit_and_environment_configurable(monkeypatch):
    created = []

    def factory(**kwargs):
        client = _Client(**kwargs)
        created.append(client)
        return client

    monkeypatch.setenv("DOCRAY_MCP_POOL_MAX_CONNECTIONS", "17")
    monkeypatch.setenv("DOCRAY_MCP_POOL_MAX_KEEPALIVE", "5")
    monkeypatch.setattr(http_client.httpx, "AsyncClient", factory)
    await http_client.acquire_client()

    limits = created[0].kwargs["limits"]
    assert isinstance(limits, httpx.Limits)
    assert limits.max_connections == 17
    assert limits.max_keepalive_connections == 5


@pytest.mark.parametrize(
    "name", ["DOCRAY_MCP_POOL_MAX_CONNECTIONS", "DOCRAY_MCP_POOL_MAX_KEEPALIVE"]
)
@pytest.mark.asyncio
async def test_pool_limits_must_be_positive(monkeypatch, name):
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError, match=f"{name} must be >= 1"):
        await http_client.acquire_client()


def _compression_probe() -> Starlette:
    async def json(_request):
        return JSONResponse({"text": "x" * 4000})

    async def sse(_request):
        async def events():
            yield b"data: " + b"x" * 4000 + b"\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/json", json), Route("/sse", sse)])
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    return app


@pytest.mark.asyncio
async def test_gzip_compresses_large_json_but_never_sse():
    transport = httpx.ASGITransport(app=_compression_probe())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        compressed = await client.get("/json", headers={"accept-encoding": "gzip"})
        streamed = await client.get("/sse", headers={"accept-encoding": "gzip"})
    assert compressed.headers["content-encoding"] == "gzip"
    assert "content-encoding" not in streamed.headers
    assert streamed.text.startswith("data: ")


@pytest.mark.asyncio
async def test_create_app_installs_gzip_and_chains_fastmcp_lifespan(monkeypatch):
    events = []

    @asynccontextmanager
    async def original_lifespan(_app):
        events.append("original-enter")
        yield
        events.append("original-exit")

    class _MCP:
        def streamable_http_app(self):
            return Starlette(lifespan=original_lifespan)

    async def close():
        events.append("client-close")

    monkeypatch.setattr(server, "build_server", lambda enable_write=False: _MCP())
    monkeypatch.setattr(server, "close_client", close)
    app = server.create_app()
    assert any(middleware.cls is GZipMiddleware for middleware in app.user_middleware)

    async with app.router.lifespan_context(app):
        assert events == ["original-enter"]
    assert events == ["original-enter", "original-exit", "client-close"]
