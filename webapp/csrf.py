"""Session-bound CSRF protection for every state-changing webapp form (BL-103).

A per-session token (signed by Flask's session cookie, compared with
``hmac.compare_digest``) is minted on first use and verified in a
``before_request`` hook that rejects every POST/PUT/PATCH/DELETE without a
matching token before the route body runs — one hook covers every mutating
route (present and future) without a per-route opt-in, since every webapp
mutation is a same-origin ``<form>`` post (no JS fetch/XHR writes exist).
"""

from __future__ import annotations

import hmac
import secrets

from flask import request, session


def csrf_token() -> str:
    """Return this session's CSRF token, minting one on first use."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["_csrf_token"] = token
    return token


#: Endpoints whose POSTs are machine-to-machine (no browser session / CSRF
#: cookie): the SAML IdP callback and the OAuth client-driven token/registration
#: endpoints. These carry their own credentials (SAML assertion, PKCE, client
#: secret) and are validated by their own routes.
_CSRF_EXEMPT_ENDPOINTS = {"saml_callback", "oauth_register", "oauth_token"}


def require_csrf_token():
    """Reject any state-changing request without this session's CSRF token."""
    if request.endpoint in _CSRF_EXEMPT_ENDPOINTS:
        return None
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token", "")
        expected = session.get("_csrf_token", "")
        if not expected or not hmac.compare_digest(sent, expected):
            return "missing or invalid CSRF token", 400
