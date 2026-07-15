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

from flask import Response, redirect, request, url_for

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


def _validate_resource(resource: str | None) -> None:
    """Reject a token bound to a different host (RFC 8707 audience protection)."""
    if not resource:
        return
    want = urlparse(_base_url()).netloc.lower()
    got = urlparse(resource).netloc.lower()
    if got and want and got != want:
        raise OAuthError(
            "invalid_target", "resource is not hosted by this server", redirectable=True
        )


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
            "grant_types_supported": ["authorization_code", "refresh_token"],
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


def oauth_authorize() -> Response | tuple[str, int]:
    """Validate the request, require login, then render the consent screen."""
    params = request.args.to_dict()
    try:
        req = _service().validate_authorization_request(params)
        _validate_resource(params.get("resource"))
    except OAuthError as exc:
        return _authorize_error(exc, params)

    record = auth_web._current_auth_session()
    if record is None:
        stash_post_login_redirect(request.full_path)
        return redirect(url_for("login"))

    return _core.render_template(
        "oauth_consent.html",
        req=req,
        scopes=req.scope.split(),
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
        _validate_resource(params.get("resource"))
    except OAuthError as exc:
        return _authorize_error(exc, params)

    if request.form.get("decision") != "approve":
        return _redirect_with_query(req.redirect_uri, error="access_denied", state=req.state)
    code = _service().issue_code(req, principal_id=record.principal_id, subject=record.subject)
    return _redirect_with_query(req.redirect_uri, code=code, state=req.state)


def _authorize_url(params: dict[str, str]) -> str:
    query = "&".join(
        f"{key}={quote(params[key])}" for key in _PASSTHROUGH_PARAMS if params.get(key)
    )
    return f"/oauth/authorize?{query}"


# --- token endpoint ---------------------------------------------------------


def _issue_tokens(principal: Principal, settings: Config) -> dict[str, object]:
    access_token = issue_access_jwt(
        principal,
        secret=settings.api_jwt_secret or "",
        issuer=settings.api_jwt_issuer,
        audience=settings.api_jwt_audience,
        ttl_seconds=settings.api_jwt_ttl_seconds,
    )
    refresh_token = issue_refresh_token(
        principal,
        secret=settings.api_jwt_secret or "",
        issuer=settings.api_jwt_issuer,
        audience=settings.api_jwt_audience,
        ttl_seconds=settings.api_refresh_token_ttl_seconds,
        store=_refresh_store(),
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": settings.api_jwt_ttl_seconds,
        "scope": " ".join(sorted(principal.scopes)),
    }


def oauth_token() -> Response:
    """Exchange an authorization code or refresh token for access + refresh tokens."""
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
            _validate_resource(request.form.get("resource"))
            principal = Principal(
                subject=grant.subject, principal_id=grant.principal_id, scopes=grant.scopes
            )
            return _json(_issue_tokens(principal, settings))
        if grant_type == "refresh_token":
            supplied = request.form.get("refresh_token", "").strip()
            if not supplied:
                return _json({"error": "invalid_request"}, status=400)
            access_token, next_refresh = rotate_refresh_token(
                supplied,
                secret=settings.api_jwt_secret,
                issuer=settings.api_jwt_issuer,
                audience=settings.api_jwt_audience,
                ttl_seconds=settings.api_refresh_token_ttl_seconds,
                access_ttl_seconds=settings.api_jwt_ttl_seconds,
                store=_refresh_store(),
            )
            principal = verify_access_jwt(
                access_token,
                secret=settings.api_jwt_secret,
                issuer=settings.api_jwt_issuer,
                audience=settings.api_jwt_audience,
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
