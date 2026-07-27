"""Async in-process ASGI client compatible with the installed Starlette stack."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

# Keep the real class when a test patches ``httpx.AsyncClient`` to prove that
# application code does (or does not) touch the network.
_AsyncClient = httpx.AsyncClient


@asynccontextmanager
async def asgi_client(app: Any, *, lifespan: bool = True) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a real in-process client, optionally running application lifespan."""
    transport = httpx.ASGITransport(app=app)
    if not lifespan:
        async with _AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
        return
    async with (
        app.router.lifespan_context(app),
        _AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        yield client
