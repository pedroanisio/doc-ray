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

Route groups live in sibling modules (D-015): :mod:`.downloads` (document/
backup/page streaming), :mod:`.job_events` (SSE), :mod:`.auth_routes` (JWT +
local auth), and :mod:`.audit_export`, alongside the pre-existing metrics/
SAML/SCIM registrars. This module keeps app construction, the request
context, and the health probes.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, cast

import anyio
import psycopg
from fastapi import FastAPI, Request, Response
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from strawberry.fastapi import GraphQLRouter

from doc_ray import __version__
from doc_ray.adapters.local_auth_postgres import PostgresLocalAuthRepository
from doc_ray.adapters.object_store import ObjectStore
from doc_ray.application.local_auth import LocalAuthService
from doc_ray.config import Config
from doc_ray.correlation import (
    ensure_request_id_from_headers,
    reset_current_request_id,
    set_current_request_id,
)
from doc_ray.entrypoints.api import health

# Re-exported for the test suite, which imports these from this module.
from doc_ray.entrypoints.api.audit_export import (
    _audit_events_csv as _audit_events_csv,
)
from doc_ray.entrypoints.api.audit_export import (
    _audit_events_json as _audit_events_json,
)
from doc_ray.entrypoints.api.audit_export import (
    _export_audit_events_endpoint as _export_audit_events_endpoint,
)
from doc_ray.entrypoints.api.auth_routes import (
    register_auth_token_route,
    register_local_auth_routes,
)
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.downloads import register_download_routes
from doc_ray.entrypoints.api.job_events import register_job_events_route
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.metrics import register_metrics_route
from doc_ray.entrypoints.api.oidc import OIDCAuthenticator, PostgresOIDCPrincipalResolver
from doc_ray.entrypoints.api.saml import register_saml_routes
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.scim import register_scim_routes
from doc_ray.entrypoints.api.security import (
    Authenticator,
    HeaderTokenAuthenticator,
    apply_principal,
    reset_principal,
)
from doc_ray.logging_setup import configure_logging


async def _readiness_checks(
    pool: AsyncConnectionPool[Any], store: health.Pingable
) -> dict[str, str]:
    """Probe every critical dependency and return a name -> status map."""
    return {
        "database": await health.probe_database(pool),
        "object_store": await anyio.to_thread.run_sync(health.probe_object_store, store),
    }


def _build_authenticator(settings: Config, pool: AsyncConnectionPool[Any]) -> Authenticator:
    """Return the configured API authenticator."""
    fallback = HeaderTokenAuthenticator(
        tokens=settings.api_tokens,
        allow_anonymous=settings.api_allow_anonymous,
        jwt_secret=settings.api_jwt_secret,
        jwt_issuer=settings.api_jwt_issuer,
        jwt_audience=settings.api_jwt_audience,
    )
    if settings.api_oidc_issuer and settings.api_oidc_jwks_json:
        jwks = json.loads(settings.api_oidc_jwks_json)
        if not isinstance(jwks, dict):
            raise ValueError("DOCRAY_API_OIDC_JWKS_JSON must be a JWKS object")
        return OIDCAuthenticator(
            issuer=settings.api_oidc_issuer,
            audience=settings.api_oidc_audience,
            jwks=jwks,
            resolve_principal=PostgresOIDCPrincipalResolver(
                pool,
                auto_create=settings.api_oidc_auto_create_principals,
            ),
            fallback=fallback if settings.api_oidc_allow_static_token_fallback else None,
            allow_anonymous=settings.api_allow_anonymous,
        )
    return fallback


async def _get_context(request: Request) -> AsyncIterator[RequestContext]:
    """Yield a per-request context: a pooled connection bound to the principal.

    The connection's role is reset before it returns to the pool, so a principal
    from one request can never leak into the next.
    """
    pool: AsyncConnectionPool = request.app.state.pool
    authenticator: Authenticator = request.app.state.authenticator
    principal = await authenticator.authenticate(request)
    request_id = ensure_request_id_from_headers(request.headers)
    request_token = set_current_request_id(request_id)
    try:
        async with pool.connection() as raw_conn:
            # The pool sets row_factory=dict_row at runtime; reflect that in the type.
            conn = cast(psycopg.AsyncConnection[dict[str, Any]], raw_conn)
            await apply_principal(conn, principal)
            try:
                yield RequestContext(
                    conn=conn,
                    principal=principal,
                    loaders=Loaders(conn),
                    request_id=request_id,
                )
            finally:
                await reset_principal(conn, principal)
    finally:
        reset_current_request_id(request_token)


def create_app(cfg: Config | None = None, authenticator: Authenticator | None = None) -> FastAPI:
    """Build the ASGI application.

    Args:
        cfg: Runtime configuration; defaults to :meth:`Config.from_env`.
        authenticator: Request authenticator; defaults to
            :class:`HeaderTokenAuthenticator` configured from environment tokens.

    Returns:
        A configured :class:`fastapi.FastAPI` app with GraphQL mounted at ``/graphql``.
    """
    configure_logging()
    settings = cfg or Config.from_env()
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
    auth = authenticator or _build_authenticator(settings, pool)

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
    # Durable rotation state (auth_refresh_tokens): restarts and scale-out no
    # longer revoke every refresh session. Tests inject InMemoryRefreshTokenStore.
    from doc_ray.adapters.refresh_token_store_postgres import PostgresRefreshTokenStore

    app.state.refresh_token_store = PostgresRefreshTokenStore(settings.database_url)
    from doc_ray.adapters.mailer_smtp import SmtpMailer

    app.state.verification_mailer = SmtpMailer.from_env()
    app.state.local_auth_service = (
        LocalAuthService(
            PostgresLocalAuthRepository(settings.database_url),
            verification_ttl=timedelta(seconds=settings.auth_email_verification_ttl_seconds),
            max_failed_logins=int(os.environ.get("DOCRAY_AUTH_MAX_FAILED_LOGINS", "10")),
            lockout_seconds=int(os.environ.get("DOCRAY_AUTH_LOCKOUT_SECONDS", "900")),
        )
        if settings.auth_local_enabled
        else None
    )
    # ``multipart_uploads_enabled`` is opt-in in Strawberry (>=0.243): without it the
    # transport rejects ``multipart/form-data`` with HTTP 400 before the resolver runs,
    # which silently breaks the ``ingestDocument`` upload mutation. Enable it and keep a
    # handle on the router so the wiring stays introspectable.
    # ``graphql_ide`` defaults to Strawberry's built-in GraphiQL explorer; gated behind
    # the same DOCRAY_API_DEBUG opt-in as schema-level introspection (BL-108) so an
    # anonymous caller (the default) can't load an interactive schema browser either.
    graphql_router = GraphQLRouter(
        build_schema(debug=settings.api_debug),
        context_getter=_get_context,
        multipart_uploads_enabled=True,
        graphql_ide="graphiql" if settings.api_debug else None,
    )
    app.state.graphql_router = graphql_router
    app.include_router(graphql_router, prefix="/graphql")
    register_metrics_route(app)
    app.add_api_route("/audit/events/export", _export_audit_events_endpoint, methods=["GET"])

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

    register_download_routes(app)
    register_auth_token_route(app, settings)
    register_local_auth_routes(app, settings)
    register_job_events_route(app)
    register_saml_routes(app)
    register_scim_routes(app, settings)

    return app


app = create_app()
