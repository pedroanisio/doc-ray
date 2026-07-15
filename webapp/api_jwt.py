"""Mint forwardable API access tokens for non-OIDC browser sessions.

Extracted from :mod:`webapp.auth_web` (D-015). Local and SAML logins produce no
upstream bearer, so — unlike OIDC — the proxied API calls (originals, page
images, covers, writes) have nothing to forward. This module mints a
self-contained API JWT the API verifies with the shared secret, giving those
sessions a forwardable credential resolved to the user's own principal for RLS.
"""

from __future__ import annotations

import os

_DEFAULT_SESSION_TTL_SECONDS = 28800


def mint_forwardable_api_jwt(principal_id: str, subject: str, scopes: frozenset[str]) -> str | None:
    """Mint a self-contained API access JWT for a locally-authenticated user.

    Local (and SAML) logins produce no upstream bearer, so the proxied API calls
    fall back to the usually-unset static service token, which 401s (the root
    cause of "broken" PDF previews under local auth). Minting an API JWT here
    gives the session a forwardable credential the API verifies with the shared
    ``DOCRAY_API_JWT_SECRET`` and resolves to the user's own principal for RLS —
    the same path an OIDC ``id_token`` takes.

    Returns ``None`` when no secret is configured, so the caller degrades to the
    legacy static-token path instead of failing the login. The TTL matches the
    browser session so previews/originals do not expire mid-session; the token
    lives only in the server-side auth-session store, never in the cookie.
    """
    secret = os.environ.get("DOCRAY_API_JWT_SECRET")
    if not secret:
        return None
    from doc_ray.entrypoints.api.security import Principal, issue_access_jwt

    issuer = os.environ.get("DOCRAY_API_JWT_ISSUER", "doc-ray").strip() or "doc-ray"
    audience = os.environ.get("DOCRAY_API_JWT_AUDIENCE", "").strip() or None
    try:
        ttl_seconds = int(
            os.environ.get("DOCRAY_WEBAPP_SESSION_TTL_SECONDS", str(_DEFAULT_SESSION_TTL_SECONDS))
        )
    except ValueError:
        ttl_seconds = _DEFAULT_SESSION_TTL_SECONDS
    return issue_access_jwt(
        Principal(subject=subject, principal_id=principal_id, scopes=scopes),
        secret=secret,
        issuer=issuer,
        audience=audience,
        ttl_seconds=ttl_seconds,
    )
