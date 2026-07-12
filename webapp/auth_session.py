"""Per-user webapp authentication session helpers.

The Flask session cookie stores only an opaque session id. Principal identity
and expiry live server-side so logout and TTL expiry invalidate access even if a
browser keeps an old signed cookie.
"""

from __future__ import annotations

import json
import os
import secrets
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from doc_ray.entrypoints.api.oidc import verify_oidc_jwt
from doc_ray.entrypoints.api.saml import SAMLAssertion, validate_saml_response


@dataclass(frozen=True)
class AuthenticatedWebPrincipal:
    """Principal identity established by an external web login flow."""

    principal_id: str
    subject: str


@dataclass(frozen=True)
class WebAuthSession:
    """Server-side auth session bound to one Flask session cookie id."""

    session_id: str
    principal_id: str
    subject: str
    issued_at: datetime
    expires_at: datetime


class InMemoryAuthSessionStore:
    """Small process-local auth session store.

    This is deliberately server-side and revocable. Deployments with multiple
    webapp replicas should swap this boundary for a shared store.
    """

    def __init__(self) -> None:
        """Initialize the empty process-local session map."""
        self._sessions: dict[str, WebAuthSession] = {}
        self._lock = Lock()

    def create(self, *, principal_id: str, subject: str, ttl_seconds: int | None = None) -> str:
        """Create a session and return its opaque id."""
        now = _utcnow()
        sid = secrets.token_urlsafe(32)
        ttl = ttl_seconds if ttl_seconds is not None else web_session_ttl_seconds()
        record = WebAuthSession(
            session_id=sid,
            principal_id=principal_id,
            subject=subject,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )
        with self._lock:
            self._sessions[sid] = record
        return sid

    def get(self, session_id: str | None) -> WebAuthSession | None:
        """Return the active session, revoking it first if it expired."""
        if not session_id:
            return None
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            if record.expires_at <= _utcnow():
                self._sessions.pop(session_id, None)
                return None
            return record

    def revoke(self, session_id: str | None) -> None:
        """Invalidate a session id if present."""
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def reset(self) -> None:
        """Clear every session; used by tests and local diagnostics."""
        with self._lock:
            self._sessions.clear()


auth_sessions = InMemoryAuthSessionStore()


def web_session_ttl_seconds(environ: Mapping[str, str] | None = None) -> int:
    """Return the server-side web auth session TTL."""
    source = environ or os.environ
    raw = source.get("DOCRAY_WEBAPP_SESSION_TTL_SECONDS", "28800")
    return max(1, int(raw))


def session_cookie_secure(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether Flask should mark the session cookie Secure."""
    source = environ or os.environ
    raw = source.get("DOCRAY_WEBAPP_SESSION_COOKIE_SECURE", "true")
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def oidc_authorization_redirect(
    *,
    redirect_uri: str,
    state: str,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Build the configured OIDC authorization URL, or None when absent."""
    source = environ or os.environ
    auth_url = source.get("DOCRAY_WEBAPP_OIDC_AUTHORIZATION_URL", "").strip()
    client_id = source.get("DOCRAY_WEBAPP_OIDC_CLIENT_ID", "").strip()
    if not auth_url or not client_id:
        return None
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": source.get("DOCRAY_WEBAPP_OIDC_SCOPE", "openid email profile"),
        "state": state,
        "nonce": secrets.token_urlsafe(24),
    }
    separator = "&" if urllib.parse.urlparse(auth_url).query else "?"
    return f"{auth_url}{separator}{urllib.parse.urlencode(params)}"


def saml_login_redirect(*, state: str, environ: Mapping[str, str] | None = None) -> str | None:
    """Build the configured SAML IdP redirect URL, or None when absent."""
    source = environ or os.environ
    sso_url = source.get("DOCRAY_WEBAPP_SAML_SSO_URL", "").strip()
    if not sso_url:
        return None
    separator = "&" if urllib.parse.urlparse(sso_url).query else "?"
    return f"{sso_url}{separator}{urllib.parse.urlencode({'RelayState': state})}"


def oidc_claims_from_code(
    *,
    code: str,
    redirect_uri: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Exchange an OIDC authorization code and verify the returned id_token."""
    source = environ or os.environ
    token_url = source.get("DOCRAY_WEBAPP_OIDC_TOKEN_URL", "").strip()
    client_id = source.get("DOCRAY_WEBAPP_OIDC_CLIENT_ID", "").strip()
    client_secret = source.get("DOCRAY_WEBAPP_OIDC_CLIENT_SECRET", "")
    issuer = (
        source.get("DOCRAY_WEBAPP_OIDC_ISSUER")
        or source.get("DOCRAY_API_OIDC_ISSUER")
        or ""
    ).strip()
    audience = (
        source.get("DOCRAY_WEBAPP_OIDC_AUDIENCE")
        or source.get("DOCRAY_API_OIDC_AUDIENCE")
        or client_id
        or None
    )
    jwks_raw = source.get("DOCRAY_WEBAPP_OIDC_JWKS_JSON") or source.get(
        "DOCRAY_API_OIDC_JWKS_JSON", ""
    )
    if not token_url or not client_id or not issuer or not jwks_raw:
        raise ValueError("webapp OIDC callback is not fully configured")
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode()
    request = urllib.request.Request(
        token_url,
        data=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    id_token = payload.get("id_token")
    if not isinstance(id_token, str):
        raise ValueError("OIDC token response did not include id_token")
    jwks = json.loads(jwks_raw)
    if not isinstance(jwks, Mapping):
        raise ValueError("OIDC JWKS must be a JSON object")
    return verify_oidc_jwt(id_token, issuer=issuer, audience=audience, jwks=jwks)


def saml_assertion_from_response(
    saml_response: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> SAMLAssertion:
    """Validate a SAMLResponse with webapp tenant/IdP configuration."""
    source = environ or os.environ
    tenant_id = source.get("DOCRAY_WEBAPP_SAML_TENANT_ID", "").strip()
    idp_entity_id = source.get("DOCRAY_WEBAPP_SAML_IDP_ENTITY_ID", "").strip()
    sp_entity_id = source.get("DOCRAY_WEBAPP_SAML_SP_ENTITY_ID", "").strip()
    allow_unsigned = source.get("DOCRAY_WEBAPP_SAML_ALLOW_UNSIGNED_ASSERTIONS", "")
    if not tenant_id or not idp_entity_id or not sp_entity_id:
        raise ValueError("webapp SAML callback is not fully configured")
    return validate_saml_response(
        saml_response,
        tenant_id=tenant_id,
        idp_entity_id=idp_entity_id,
        service_provider_entity_id=sp_entity_id,
        allow_unsigned=allow_unsigned.strip().lower() in {"1", "true", "yes", "on"},
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)
