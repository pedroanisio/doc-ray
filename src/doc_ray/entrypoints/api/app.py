"""FastAPI ASGI application: GraphQL router + async connection-pool lifecycle.

Serves the read-only GraphQL API at ``/graphql`` and a ``/health`` liveness probe.
An :class:`~psycopg_pool.AsyncConnectionPool` is opened on startup and closed on
shutdown; each request borrows one connection, binds the authenticated principal to
it (so RLS applies), and returns it — resetting the role — on the way out.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import psycopg
from fastapi import FastAPI, Request
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from strawberry.fastapi import GraphQLRouter

from doc_ray.config import Config
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import (
    Authenticator,
    NoopAuthenticator,
    apply_principal,
    reset_principal,
)


async def _get_context(request: Request) -> AsyncIterator[RequestContext]:
    """Yield a per-request context: a pooled connection bound to the principal.

    The connection's role is reset before it returns to the pool, so a principal
    from one request can never leak into the next.
    """
    pool: AsyncConnectionPool = request.app.state.pool
    authenticator: Authenticator = request.app.state.authenticator
    principal = await authenticator.authenticate(request)
    async with pool.connection() as raw_conn:
        # The pool sets row_factory=dict_row at runtime; reflect that in the type.
        conn = cast(psycopg.AsyncConnection[dict[str, Any]], raw_conn)
        await apply_principal(conn, principal)
        try:
            yield RequestContext(conn=conn, principal=principal, loaders=Loaders(conn))
        finally:
            await reset_principal(conn, principal)


def create_app(cfg: Config | None = None, authenticator: Authenticator | None = None) -> FastAPI:
    """Build the ASGI application.

    Args:
        cfg: Runtime configuration; defaults to :meth:`Config.from_env`.
        authenticator: Request authenticator; defaults to :class:`NoopAuthenticator`
            (anonymous, unfiltered — RLS bypassed on the trusted connection).

    Returns:
        A configured :class:`fastapi.FastAPI` app with GraphQL mounted at ``/graphql``.
    """
    settings = cfg or Config.from_env()
    auth = authenticator or NoopAuthenticator()
    pool = AsyncConnectionPool(
        settings.database_url,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True},
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await pool.open()
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title="doc-ray API", lifespan=lifespan)
    app.state.pool = pool
    app.state.authenticator = auth
    app.include_router(
        GraphQLRouter(build_schema(), context_getter=_get_context), prefix="/graphql"
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    return app


app = create_app()
