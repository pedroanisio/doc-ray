"""One outbound HTTP connection pool per MCP process.

The GraphQL client is shared to amortize connection and TLS setup across the
small sequential calls doc-ray teaches agents to make. Caller credentials are
never stored on the client: every tool call supplies its bearer token in that
request's headers, preserving principal isolation on the shared pool.
"""

from __future__ import annotations

import asyncio
import os

import httpx

_DEFAULT_TIMEOUT_SECONDS = 30.0

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


def _positive_int(value: str, name: str) -> int:
    """Read a positive integer environment setting or fail at first use."""
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{name} must be >= 1")
    return parsed


def _build_client() -> httpx.AsyncClient:
    """Construct the token-free process client with explicit pool bounds."""
    limits = httpx.Limits(
        max_connections=_positive_int(
            os.environ.get("DOCRAY_MCP_POOL_MAX_CONNECTIONS", "100"),
            "DOCRAY_MCP_POOL_MAX_CONNECTIONS",
        ),
        max_keepalive_connections=_positive_int(
            os.environ.get("DOCRAY_MCP_POOL_MAX_KEEPALIVE", "20"),
            "DOCRAY_MCP_POOL_MAX_KEEPALIVE",
        ),
    )
    timeout = float(os.environ.get("DOCRAY_MCP_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS)))
    return httpx.AsyncClient(timeout=timeout, limits=limits)


async def acquire_client() -> httpx.AsyncClient:
    """Return the process-wide client, constructing it once under contention."""
    global _client
    async with _client_lock:
        if _client is None:
            _client = _build_client()
        return _client


async def close_client() -> None:
    """Close and clear the cached client; safe when none has been acquired."""
    global _client
    async with _client_lock:
        client, _client = _client, None
        if client is not None:
            await client.aclose()
