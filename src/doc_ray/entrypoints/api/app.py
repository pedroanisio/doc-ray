"""FastAPI ASGI application: GraphQL router + async connection-pool lifecycle.

Serves the GraphQL API (reads plus enqueue/upload mutations) at ``/graphql``, a
``/health`` liveness probe, and a ``/readyz`` readiness probe that checks Postgres and
the object store (so a store the API cannot reach is surfaced as HTTP 503 / an
``unhealthy`` container, not a cryptic error at the first upload).
An :class:`~psycopg_pool.AsyncConnectionPool` is opened on startup and closed on
shutdown; each request borrows one connection, binds the authenticated principal to
it (so RLS applies), and returns it — resetting the role — on the way out. Every
pooled connection carries a Postgres ``statement_timeout`` (``api_statement_timeout_ms``)
so a single unbounded query cannot monopolize a backend indefinitely.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import anyio
import psycopg
from fastapi import FastAPI, Request, Response
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from strawberry.fastapi import GraphQLRouter

from doc_ray import __version__
from doc_ray.adapters.object_store import ObjectStore
from doc_ray.config import Config
from doc_ray.entrypoints.api import health
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import (
    Authenticator,
    HeaderTokenAuthenticator,
    apply_principal,
    reset_principal,
)


async def _readiness_checks(
    pool: AsyncConnectionPool[Any], store: health.Pingable
) -> dict[str, str]:
    """Probe every critical dependency and return a name -> status map."""
    return {
        "database": await health.probe_database(pool),
        "object_store": await anyio.to_thread.run_sync(health.probe_object_store, store),
    }


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
        authenticator: Request authenticator; defaults to
            :class:`HeaderTokenAuthenticator` configured from environment tokens.

    Returns:
        A configured :class:`fastapi.FastAPI` app with GraphQL mounted at ``/graphql``.
    """
    settings = cfg or Config.from_env()
    auth = authenticator or HeaderTokenAuthenticator(
        tokens=settings.api_tokens,
        allow_anonymous=settings.api_allow_anonymous,
    )
    store = ObjectStore(settings)
    pool = AsyncConnectionPool(
        settings.database_url,
        open=False,
        min_size=settings.api_pool_min_size,
        max_size=settings.api_pool_max_size,
        timeout=settings.api_pool_timeout,
        kwargs={
            "row_factory": dict_row,
            "autocommit": True,
            "options": f"-c statement_timeout={settings.api_statement_timeout_ms}",
        },
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
    app.state.object_store = store
    # ``multipart_uploads_enabled`` is opt-in in Strawberry (>=0.243): without it the
    # transport rejects ``multipart/form-data`` with HTTP 400 before the resolver runs,
    # which silently breaks the ``ingestDocument`` upload mutation. Enable it and keep a
    # handle on the router so the wiring stays introspectable.
    graphql_router = GraphQLRouter(
        build_schema(),
        context_getter=_get_context,
        multipart_uploads_enabled=True,
    )
    app.state.graphql_router = graphql_router
    app.include_router(graphql_router, prefix="/graphql")

    @app.get("/health")
    async def health_endpoint() -> dict[str, str]:
        """Liveness probe: the process is up. Cheap and unconditional (no dependencies)."""
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz(request: Request, response: Response) -> dict[str, Any]:
        """Readiness probe: 200 only when Postgres AND the object store are reachable.

        Returns 503 with a per-dependency diagnostic otherwise, so a misconfigured
        object store (the classic ``S3_ENDPOINT`` pointing nowhere) is caught here —
        by a healthcheck or a curl — rather than at the first ``ingestDocument`` upload.
        Reads dependencies off ``app.state`` so deployments (and tests) can swap them.
        """
        report = health.readiness(
            await _readiness_checks(request.app.state.pool, request.app.state.object_store)
        )
        report["version"] = __version__
        if not report["ready"]:
            response.status_code = 503
        return report

    @app.get("/backups/{backup_id}/download")
    async def download_backup(backup_id: str, request: Request) -> Response:
        """Stream a catalogued backup archive from the object store.

        The object store sits inside the API's network/trust boundary, so the
        browser-facing webapp proxies downloads through here rather than reaching
        the store directly — a host-side ``S3_ENDPOINT`` mismatch can never break
        the download. Only ``available`` archives are served; anything else is a
        404. The blocking ``get`` runs off the event loop.
        """
        pool: AsyncConnectionPool = request.app.state.pool
        store: ObjectStore = request.app.state.object_store
        async with pool.connection() as raw_conn:
            conn = cast(psycopg.AsyncConnection[dict[str, Any]], raw_conn)
            cur = await conn.execute(
                "SELECT object_key FROM backups WHERE id = %s AND status = 'available'",
                (backup_id,),
            )
            row = await cur.fetchone()
        if row is None or not row["object_key"]:
            return Response(status_code=404)
        data = await anyio.to_thread.run_sync(store.get, str(row["object_key"]))
        return Response(
            content=data,
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{backup_id}.tar.gz"'},
        )

    return app


app = create_app()
