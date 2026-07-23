"""Per-tool-call audit logging: caller fingerprint, duration, outcome — never the raw token."""

from __future__ import annotations

import inspect
import logging

import pytest

from doc_ray.entrypoints.mcp import audit, corpus


@pytest.fixture(autouse=True)
def _reset_token():
    corpus.caller_token.set("")
    yield


@pytest.mark.asyncio
async def test_audited_success_logs_tool_caller_duration_not_token(caplog):
    async def echo(text: str) -> str:
        """Echo back."""
        return text

    corpus.caller_token.set("secret-token-abc")
    with caplog.at_level(logging.INFO, logger="doc_ray.mcp.audit"):
        assert await audit.audited(echo)(text="hi") == "hi"

    (record,) = caplog.records
    message = record.getMessage()
    assert "tool=echo" in message
    assert "outcome=ok" in message
    assert "duration_ms=" in message
    assert "caller=" in message and "caller=anonymous" not in message
    assert "secret-token-abc" not in message  # only a fingerprint is ever logged


@pytest.mark.asyncio
async def test_audited_anonymous_caller_is_labeled(caplog):
    async def ping() -> str:
        return "pong"

    with caplog.at_level(logging.INFO, logger="doc_ray.mcp.audit"):
        await audit.audited(ping)()
    assert "caller=anonymous" in caplog.records[0].getMessage()


@pytest.mark.asyncio
async def test_audited_typed_error_logs_kind_and_reraises(caplog):
    async def boom() -> None:
        raise corpus.ToolCallError("permission_denied", "nope")

    with (
        caplog.at_level(logging.INFO, logger="doc_ray.mcp.audit"),
        pytest.raises(corpus.ToolCallError),
    ):
        await audit.audited(boom)()

    message = caplog.records[0].getMessage()
    assert "outcome=error" in message
    assert "error=permission_denied" in message


@pytest.mark.asyncio
async def test_audited_unexpected_error_logs_exception_type(caplog):
    async def boom() -> None:
        raise ValueError("x")

    with (
        caplog.at_level(logging.INFO, logger="doc_ray.mcp.audit"),
        pytest.raises(ValueError),
    ):
        await audit.audited(boom)()
    assert "error=ValueError" in caplog.records[0].getMessage()


@pytest.mark.asyncio
async def test_audited_clips_long_argument_values(caplog):
    async def q(term: str) -> str:
        return "ok"

    with caplog.at_level(logging.INFO, logger="doc_ray.mcp.audit"):
        await audit.audited(q)(term="z" * 500)

    message = caplog.records[0].getMessage()
    assert "z" * 500 not in message  # summaries are clipped
    assert "z" * 40 in message  # but a useful prefix survives


def test_audited_preserves_metadata_for_schema_derivation():
    # FastMCP derives the tool name, description, and input schema from the
    # function's name, docstring, annotations, and signature — wrapping must
    # not disturb any of them.
    async def sample(term: str, limit: int = 5) -> list:
        """Sample tool."""
        return []

    wrapped = audit.audited(sample)
    assert wrapped.__name__ == "sample"
    assert wrapped.__doc__ == "Sample tool."
    assert wrapped.__annotations__ == sample.__annotations__
    assert str(inspect.signature(wrapped)) == str(inspect.signature(sample))
