"""The forwarded bearer token tracks the current request, not initialization.

Stateless Streamable HTTP lets initialization and later tool calls land on
different processes. These tests pin authorization to the request middleware just
validated, including credential rotation, while retaining the stdio fallback.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from starlette.requests import Request

from doc_ray.entrypoints.mcp import audit, corpus, tokens


class _FakeResp:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"data": {"searchSentences": []}}


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, url: str, json: dict[str, object], headers: dict[str, str]) -> _FakeResp:
        self.calls.append(headers)
        return _FakeResp()


def _request(*headers: tuple[bytes, bytes]) -> Request:
    """Build a realistic ASGI request carrying *headers*."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "query_string": b"",
            "scheme": "http",
            "server": ("mcp", 8080),
            "headers": list(headers),
        }
    )


@contextlib.contextmanager
def _dispatching(request: Request | None) -> Iterator[None]:
    """Bind *request* as the message the MCP server is currently dispatching."""
    token = request_ctx.set(
        RequestContext(
            request_id=1,
            meta=None,
            session=None,
            lifespan_context=None,
            request=request,
        )
    )
    try:
        yield
    finally:
        request_ctx.reset(token)


@pytest.fixture(autouse=True)
def _reset_token() -> Iterator[None]:
    tokens.caller_token.set("")
    yield
    tokens.caller_token.set("")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(corpus, "acquire_client", AsyncMock(return_value=fake))
    return fake


@pytest.mark.asyncio
async def test_tool_forwards_the_current_requests_token_not_the_sessions(
    client: _FakeClient,
) -> None:
    """A refreshed credential reaches GraphQL even when initialization used an older one."""
    tokens.caller_token.set("stale-token-from-initialize")
    with _dispatching(_request((b"authorization", b"Bearer refreshed-token"))):
        await corpus.search_sentences("Berlin", limit=1)
    assert client.calls[0]["Authorization"] == "Bearer refreshed-token"


@pytest.mark.asyncio
async def test_x_docray_header_on_the_current_request_is_honoured(client: _FakeClient) -> None:
    """The alternate credential header is read from the live request too."""
    tokens.caller_token.set("stale-token-from-initialize")
    with _dispatching(_request((b"x-docray-api-token", b"refreshed-token"))):
        await corpus.search_sentences("Berlin", limit=1)
    assert client.calls[0]["Authorization"] == "Bearer refreshed-token"


@pytest.mark.asyncio
async def test_anonymous_current_request_never_reuses_a_session_token(
    client: _FakeClient,
) -> None:
    """An uncredentialed request forwards nothing — a stale token is not a fallback."""
    tokens.caller_token.set("stale-token-from-initialize")
    with _dispatching(_request()):
        await corpus.search_sentences("Berlin", limit=1)
    assert "Authorization" not in client.calls[0]


@pytest.mark.asyncio
async def test_falls_back_to_the_contextvar_without_an_http_request(client: _FakeClient) -> None:
    """Transports with no HTTP request (stdio) keep using the middleware's contextvar."""
    tokens.caller_token.set("stdio-token")
    with _dispatching(None):
        await corpus.search_sentences("Berlin", limit=1)
    assert client.calls[0]["Authorization"] == "Bearer stdio-token"


@pytest.mark.asyncio
async def test_audit_fingerprint_tracks_the_current_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The audit line fingerprints the credential actually forwarded, not the session's."""
    import hashlib

    async def tool() -> str:
        return "ok"

    tokens.caller_token.set("stale-token-from-initialize")
    with (
        caplog.at_level("INFO", logger="doc_ray.mcp.audit"),
        _dispatching(_request((b"authorization", b"Bearer refreshed-token"))),
    ):
        await audit.audited(tool)()
    expected = hashlib.sha256(b"refreshed-token").hexdigest()[:12]
    assert expected in caplog.text
