"""OAuth 2.1 Authorization Server routes — the MCP connector's sign-in front door.

The webapp is the Authorization Server (it owns human login/sessions); the MCP
server is the Resource Server (it validates the tokens minted here). A client
such as Claude Desktop drives:

    GET  /.well-known/oauth-protected-resource   (served for the MCP resource)
    GET  /.well-known/oauth-authorization-server (this AS's metadata)
    POST /oauth/register                         (Dynamic Client Registration)
    GET  /oauth/authorize                        (browser login + consent, PKCE)
    POST /oauth/token                            (authorization_code / refresh)

Tokens are the same RLS-bound doc-ray access JWTs the API already issues
(``issue_access_jwt`` / ``issue_refresh_token``), so nothing about API/MCP
authorization changes — this only adds a standards-based way to obtain one.
The security-critical rules (PKCE, redirect matching, single-use codes) live in
:mod:`doc_ray.application.oauth_server`; this module is the HTTP/consent glue.
"""

from __future__ import annotations

import json
import os
from types import ModuleType
from urllib.parse import quote, urlparse

from flask import Response, current_app, redirect, request, url_for

from doc_ray.adapters.oauth_store_postgres import (
    PostgresAuthorizationCodeStore,
    PostgresOAuthClientStore,
)
from doc_ray.adapters.refresh_token_store_postgres import PostgresRefreshTokenStore
from doc_ray.application.oauth_server import (
    SUPPORTED_SCOPES,
    OAuthError,
    OAuthServerService,
)
from doc_ray.config import Config
from doc_ray.entrypoints.api.security import (
    Principal,
    issue_access_jwt,
    issue_refresh_token,
    rotate_refresh_token,
    verify_access_jwt,
)

try:
    from webapp import auth_web
    from webapp.login_redirect import stash_post_login_redirect
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    import auth_web  # type: ignore[no-redef]
    from login_redirect import stash_post_login_redirect

_core: ModuleType
_SERVICE: OAuthServerService | None = None
_REFRESH_STORE: PostgresRefreshTokenStore | None = None

#: OAuth endpoints reachable without a browser session. ``/oauth/authorize``
#: is here too: it handles its own login (stash + redirect) rather than being
#: bounced by the generic auth gate, which would drop the return URL.
_OAUTH_PUBLIC_ENDPOINTS = {
    "oauth_protected_resource_metadata",
    "oauth_authorization_server_metadata",
    "oauth_register",
    "oauth_authorize",
    "oauth_authorize_submit",
    "oauth_token",
}
#: Machine POSTs (no browser, no CSRF cookie) that the CSRF hook must skip.
OAUTH_CSRF_EXEMPT_ENDPOINTS = {"oauth_register", "oauth_token"}

_PASSTHROUGH_PARAMS = (
    "client_id",
    "redirect_uri",
    "response_type",
    "code_challenge",
    "code_challenge_method",
    "scope",
    "state",
    "resource",
)


def _service() -> OAuthServerService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = OAuthServerService(
            PostgresOAuthClientStore(_core.DATABASE_URL),
            PostgresAuthorizationCodeStore(_core.DATABASE_URL),
        )
    return _SERVICE


def _refresh_store() -> PostgresRefreshTokenStore:
    global _REFRESH_STORE
    if _REFRESH_STORE is None:
        _REFRESH_STORE = PostgresRefreshTokenStore(_core.DATABASE_URL)
    return _REFRESH_STORE


def _settings() -> Config:
    return Config.from_env()


def _base_url() -> str:
    base = os.environ.get("DOCRAY_PUBLIC_BASE_URL", "").strip().rstrip("/")
    return base or request.host_url.rstrip("/")


def _resource() -> str:
    res = os.environ.get("DOCRAY_MCP_RESOURCE", "").strip().rstrip("/")
    return res or f"{_base_url()}/mcp"


def _json(payload: dict[str, object], status: int = 200) -> Response:
    return Response(
        json.dumps(payload),
        status=status,
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


def _oauth_error_json(exc: OAuthError) -> Response:
    return _json({"error": exc.error, "error_description": exc.description}, status=exc.status_code)


def _resolve_resource(resource: str | None) -> str:
    """Return the resource URI to bind a token's audience to (RFC 8707).

    Defaults to this server's canonical MCP resource when the client omits it, so
    every issued token is bound to a specific server (``aud``), never a generic
    shared audience. A resource on a foreign host is rejected — a token must never
    be mintable for someone else's server.
    """
    if not resource:
        return _resource()
    want = urlparse(_base_url()).netloc.lower()
    got = urlparse(resource).netloc.lower()
    if got and want and got != want:
        raise OAuthError(
            "invalid_target", "resource is not hosted by this server", redirectable=True
        )
    return resource.rstrip("/")


# --- discovery metadata -----------------------------------------------------


def oauth_protected_resource_metadata() -> Response:
    """RFC 9728 metadata for the MCP resource server."""
    return _json(
        {
            "resource": _resource(),
            "authorization_servers": [_base_url()],
            "scopes_supported": list(SUPPORTED_SCOPES),
            "bearer_methods_supported": ["header"],
        }
    )


def oauth_authorization_server_metadata() -> Response:
    """RFC 8414 metadata advertising this authorization server's endpoints."""
    base = _base_url()
    return _json(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": [
                "authorization_code",
                "refresh_token",
                "client_credentials",
            ],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "scopes_supported": list(SUPPORTED_SCOPES),
        }
    )


# --- dynamic client registration --------------------------------------------


def oauth_register() -> Response:
    """RFC 7591 Dynamic Client Registration."""
    metadata = request.get_json(silent=True) or {}
    if not isinstance(metadata, dict):
        return _json({"error": "invalid_client_metadata"}, status=400)
    try:
        client = _service().register_client(metadata)
    except OAuthError as exc:
        return _oauth_error_json(exc)
    body: dict[str, object] = {
        "client_id": client.client_id,
        "redirect_uris": list(client.redirect_uris),
        "token_endpoint_auth_method": client.token_endpoint_auth_method,
        "grant_types": list(client.grant_types),
        "scope": client.scope,
    }
    if client.client_name:
        body["client_name"] = client.client_name
    if client.client_secret:
        body["client_secret"] = client.client_secret
    return _json(body, status=201)


# --- authorization endpoint -------------------------------------------------


def _redirect_with_query(redirect_uri: str, **params: str | None) -> Response:
    pairs = [f"{key}={quote(value)}" for key, value in params.items() if value]
    separator = "&" if "?" in redirect_uri else "?"
    return redirect(f"{redirect_uri}{separator}{'&'.join(pairs)}")


def _authorize_error(exc: OAuthError, params: dict[str, str]) -> Response | tuple[str, int]:
    redirect_uri = params.get("redirect_uri")
    if exc.redirectable and redirect_uri:
        return _redirect_with_query(redirect_uri, error=exc.error, state=params.get("state"))
    return (
        _core.render_template(
            "http_error.html",
            code=exc.status_code,
            title="Authorization error",
            message=exc.description or exc.error,
            back_url="/",
            back_label="Back to doc·ray",
        ),
        exc.status_code,
    )


def _principal_scopes(principal_id: str) -> frozenset[str]:
    """The RBAC-derived scopes the logged-in user may grant to a connector.

    Bounds an issued OAuth token to the user's real capability (D-027) so ``write``
    reaches only a principal whose permissions carry it; a ``reader`` — or any
    principal we cannot resolve (misconfig, non-local identity with no RBAC row) —
    degrades safely to read-only.
    """
    try:
        return auth_web._local_auth_service().scopes_for_principal(principal_id)
    except Exception:  # pragma: no cover - defensive: never fail-open to write
        current_app.logger.exception("OAuth: could not resolve principal scopes")
        return frozenset({"read"})


def _grantable_scopes(principal_id: str) -> list[str]:
    """The scopes the consent screen shows and the code will actually carry."""
    return sorted(_principal_scopes(principal_id) & set(SUPPORTED_SCOPES))


def oauth_authorize() -> Response | tuple[str, int]:
    """Validate the request, require login, then render the consent screen."""
    params = request.args.to_dict()
    try:
        req = _service().validate_authorization_request(params)
        _resolve_resource(params.get("resource"))
    except OAuthError as exc:
        return _authorize_error(exc, params)

    record = auth_web._current_auth_session()
    if record is None:
        stash_post_login_redirect(request.full_path)
        return redirect(url_for("login"))

    return _core.render_template(
        "oauth_consent.html",
        req=req,
        # Show what will actually be granted (the user's capability), not merely
        # what the client requested — so consent is truthful about write access.
        scopes=_grantable_scopes(record.principal_id),
        subject=record.subject,
        passthrough={key: params.get(key, "") for key in _PASSTHROUGH_PARAMS},
    )


def oauth_authorize_submit() -> Response | tuple[str, int]:
    """Handle the consent decision: issue a code on approve, else access_denied."""
    params = request.form.to_dict()
    record = auth_web._current_auth_session()
    if record is None:
        # Session lapsed on the consent screen — re-authenticate and resume.
        stash_post_login_redirect(_authorize_url(params))
        return redirect(url_for("login"))
    try:
        req = _service().validate_authorization_request(params)
        _resolve_resource(params.get("resource"))
    except OAuthError as exc:
        return _authorize_error(exc, params)

    if request.form.get("decision") != "approve":
        return _redirect_with_query(req.redirect_uri, error="access_denied", state=req.state)
    code = _service().issue_code(
        req,
        principal_id=record.principal_id,
        subject=record.subject,
        principal_scopes=_principal_scopes(record.principal_id),
    )
    return _redirect_with_query(req.redirect_uri, code=code, state=req.state)


def _authorize_url(params: dict[str, str]) -> str:
    query = "&".join(
        f"{key}={quote(params[key])}" for key in _PASSTHROUGH_PARAMS if params.get(key)
    )
    return f"/oauth/authorize?{query}"


# --- token endpoint ---------------------------------------------------------


def _issue_tokens(
    principal: Principal, settings: Config, resource: str, *, with_refresh: bool = True
) -> dict[str, object]:
    """Mint an access token (and, unless suppressed, a refresh token).

    ``aud`` is bound to *resource* (RFC 8707), so the token is scoped to one MCP
    server and cannot be redeemed against another. ``with_refresh`` is False for
    the client_credentials grant (OAuth 2.1 issues no refresh token there).
    """
    access_token = issue_access_jwt(
        principal,
        secret=settings.api_jwt_secret or "",
        issuer=settings.api_jwt_issuer,
        audience=resource,
        ttl_seconds=settings.api_jwt_ttl_seconds,
    )
    body: dict[str, object] = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": settings.api_jwt_ttl_seconds,
        "scope": " ".join(sorted(principal.scopes)),
    }
    if with_refresh:
        body["refresh_token"] = issue_refresh_token(
            principal,
            secret=settings.api_jwt_secret or "",
            issuer=settings.api_jwt_issuer,
            audience=resource,
            ttl_seconds=settings.api_refresh_token_ttl_seconds,
            store=_refresh_store(),
        )
    return body


def oauth_token() -> Response:
    """Exchange an authorization code, refresh token, or client credentials for tokens."""
    settings = _settings()
    if not settings.api_jwt_secret:
        return _json(
            {
                "error": "temporarily_unavailable",
                "error_description": "token issuance not configured",
            },
            status=503,
        )
    grant_type = request.form.get("grant_type", "")
    try:
        if grant_type == "authorization_code":
            grant = _service().exchange_authorization_code(request.form.to_dict())
            resource = _resolve_resource(grant.resource or request.form.get("resource"))
            principal = Principal(
                subject=grant.subject, principal_id=grant.principal_id, scopes=grant.scopes
            )
            return _json(_issue_tokens(principal, settings, resource))
        if grant_type == "client_credentials":
            grant = _service().exchange_client_credentials(request.form.to_dict())
            resource = _resolve_resource(grant.resource or request.form.get("resource"))
            principal = Principal(
                subject=grant.subject, principal_id=grant.principal_id, scopes=grant.scopes
            )
            return _json(_issue_tokens(principal, settings, resource, with_refresh=False))
        if grant_type == "refresh_token":
            supplied = request.form.get("refresh_token", "").strip()
            if not supplied:
                return _json({"error": "invalid_request"}, status=400)
            resource = _resolve_resource(request.form.get("resource"))
            access_token, next_refresh = rotate_refresh_token(
                supplied,
                secret=settings.api_jwt_secret,
                issuer=settings.api_jwt_issuer,
                audience=resource,
                ttl_seconds=settings.api_refresh_token_ttl_seconds,
                access_ttl_seconds=settings.api_jwt_ttl_seconds,
                store=_refresh_store(),
            )
            principal = verify_access_jwt(
                access_token,
                secret=settings.api_jwt_secret,
                issuer=settings.api_jwt_issuer,
                audience=resource,
            )
            return _json(
                {
                    "access_token": access_token,
                    "refresh_token": next_refresh,
                    "token_type": "Bearer",
                    "expires_in": settings.api_jwt_ttl_seconds,
                    "scope": " ".join(sorted(principal.scopes)),
                }
            )
        return _json({"error": "unsupported_grant_type"}, status=400)
    except OAuthError as exc:
        return _oauth_error_json(exc)
    except ValueError as exc:
        return _json({"error": "invalid_grant", "error_description": str(exc)}, status=400)


def register(app, core: ModuleType) -> None:
    """Attach the OAuth 2.1 Authorization Server routes to *app*."""
    global _core
    _core = core
    # These endpoints are machine- or self-gated; exempt them from the browser
    # auth gate (and, for the machine POSTs, from CSRF).
    auth_web._AUTH_PUBLIC_ENDPOINTS.update(_OAUTH_PUBLIC_ENDPOINTS)

    app.add_url_rule(
        "/.well-known/oauth-protected-resource",
        "oauth_protected_resource_metadata",
        oauth_protected_resource_metadata,
    )
    app.add_url_rule(
        "/.well-known/oauth-protected-resource/mcp",
        "oauth_protected_resource_metadata",
        oauth_protected_resource_metadata,
    )
    app.add_url_rule(
        "/.well-known/oauth-authorization-server",
        "oauth_authorization_server_metadata",
        oauth_authorization_server_metadata,
    )
    app.add_url_rule("/oauth/register", "oauth_register", oauth_register, methods=["POST"])
    app.add_url_rule("/oauth/authorize", "oauth_authorize", oauth_authorize)
    app.add_url_rule(
        "/oauth/authorize", "oauth_authorize_submit", oauth_authorize_submit, methods=["POST"]
    )
    app.add_url_rule("/oauth/token", "oauth_token", oauth_token, methods=["POST"])
