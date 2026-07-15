"""Regression cover: the webapp forwards the logged-in user's credential to the API.

Document page images and original downloads are proxied through the API, which
enforces row-level security against the *caller's* principal. In the production
auth posture (OIDC, ``DOCRAY_API_OIDC_ALLOW_STATIC_TOKEN_FALLBACK=false``) the
webapp must present the USER's own verified bearer token, or the API sees an
anonymous caller, ``docray_readable_documents()`` returns nothing, and every
image/download 404s (the "broken images on the web" failure).

These lock the header-building contract so that regression cannot return:
a logged-in OIDC user's token is forwarded; anonymous callers send no auth; and
the legacy static-token + act-as path is used only as a fallback.
"""

from __future__ import annotations

import pytest
from flask import session

import webapp.auth_web as auth_web
from webapp import app as webapp
from webapp import graphql_client
from webapp.auth_session import auth_sessions


def _login(*, bearer: str | None, principal_id: str = "p-1") -> str:
    auth_sessions.reset()
    return auth_sessions.create(
        principal_id=principal_id, subject="oidc:alice", bearer_token=bearer
    )


def test_web_auth_session_persists_bearer_token():
    auth_sessions.reset()
    sid = auth_sessions.create(principal_id="p", subject="s", bearer_token="tok")
    record = auth_sessions.get(sid)
    assert record is not None and record.bearer_token == "tok"


def test_api_auth_headers_forwards_logged_in_user_bearer_token(monkeypatch):
    monkeypatch.setattr(graphql_client, "API_TOKEN", "service-token")  # must NOT win
    sid = _login(bearer="user-jwt-123", principal_id="p-42")
    with webapp.app.test_request_context("/"):
        session["_auth_sid"] = sid
        headers = graphql_client.api_auth_headers()
    assert headers["Authorization"] == "Bearer user-jwt-123"
    # The token identifies the user; no act-as impersonation is added.
    assert "X-DocRay-Act-As" not in headers


def test_api_auth_headers_anonymous_sends_no_authorization(monkeypatch):
    monkeypatch.setattr(graphql_client, "API_TOKEN", None)
    auth_sessions.reset()
    with webapp.app.test_request_context("/"):
        headers = graphql_client.api_auth_headers()
    assert "Authorization" not in headers
    assert "X-DocRay-Act-As" not in headers


def test_api_auth_headers_falls_back_to_service_token_and_act_as(monkeypatch):
    # A session without a forwardable bearer (e.g. SAML) uses the legacy path.
    monkeypatch.setattr(graphql_client, "API_TOKEN", "service-token")
    sid = _login(bearer=None, principal_id="p-9")
    with webapp.app.test_request_context("/"):
        session["_auth_sid"] = sid
        headers = graphql_client.api_auth_headers()
    assert headers["Authorization"] == "Bearer service-token"
    assert headers["X-DocRay-Act-As"] == "p-9"


def test_complete_oidc_login_retains_verified_id_token(monkeypatch):
    monkeypatch.setattr(
        auth_web, "oidc_claims_from_code", lambda **_k: ({"sub": "alice"}, "id-token-xyz")
    )
    monkeypatch.setattr(auth_web, "_resolve_web_principal", lambda _subject: "p-oidc")
    principal = auth_web.complete_oidc_login("code", "https://cb")
    assert principal.principal_id == "p-oidc"
    assert principal.bearer_token == "id-token-xyz"


def test_complete_saml_login_has_no_forwardable_bearer(monkeypatch):
    class _Assertion:
        external_subject = "saml:bob"

    monkeypatch.setattr(auth_web, "saml_assertion_from_response", lambda _resp: _Assertion())
    monkeypatch.setattr(auth_web, "_resolve_web_principal", lambda _subject: "p-saml")
    principal = auth_web.complete_saml_login("response")
    assert principal.principal_id == "p-saml"
    assert principal.bearer_token is None


def test_db_role_guard_rejects_client_role_under_browser_auth():
    """The webapp must refuse to boot as docray_client when browser auth is on.

    Only docray_api resolves the per-session app.principal_id, so docray_client +
    browser auth = a silently empty corpus. The guard turns that into a loud
    boot failure. Regression for the DOCRAY_DB_ROLE drift that hid all documents.
    """
    from webapp.app import _assert_db_role_resolves_principals

    with pytest.raises(RuntimeError, match="docray_api"):
        _assert_db_role_resolves_principals("docray_client", browser_auth_required=True)


def test_db_role_guard_allows_api_role_and_no_auth():
    from webapp.app import _assert_db_role_resolves_principals

    # docray_api under browser auth is the supported per-user config.
    _assert_db_role_resolves_principals("docray_api", browser_auth_required=True)
    # Without browser auth, docray_client (single-principal/dev) is fine.
    _assert_db_role_resolves_principals("docray_client", browser_auth_required=False)
