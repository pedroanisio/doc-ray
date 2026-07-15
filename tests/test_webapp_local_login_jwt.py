"""Regression cover: local (non-OIDC) login mints a forwardable API JWT.

The bug: under local auth the session carried no bearer token, so proxied
originals/page-images fell back to the (unset) static service token and 401'd —
PDF previews rendered broken. The fix mints a self-contained API access JWT at
login (verified by the API with the shared DOCRAY_API_JWT_SECRET) and stores it
as the session bearer, exactly like the OIDC id_token path.

These lock the contract: the minted token round-trips through the API's own
verifier, carries the user's principal + scopes, is attached by
complete_local_login, and is then forwarded by api_auth_headers.
"""

from __future__ import annotations

import pytest
from flask import session

import webapp.auth_web as auth_web
from doc_ray.application.local_auth import LocalPrincipal
from doc_ray.entrypoints.api.security import verify_access_jwt
from webapp import app as webapp
from webapp import graphql_client
from webapp.api_jwt import mint_forwardable_api_jwt
from webapp.auth_session import auth_sessions

_SECRET = "unit-test-jwt-secret"


@pytest.fixture
def jwt_env(monkeypatch):
    monkeypatch.setenv("DOCRAY_API_JWT_SECRET", _SECRET)
    monkeypatch.setenv("DOCRAY_API_JWT_ISSUER", "doc-ray")
    monkeypatch.delenv("DOCRAY_API_JWT_AUDIENCE", raising=False)


def test_mint_round_trips_through_the_api_verifier(jwt_env):
    token = mint_forwardable_api_jwt("p-1", "local:alice@x.com", frozenset({"read"}))
    assert token is not None
    principal = verify_access_jwt(token, secret=_SECRET, issuer="doc-ray", audience=None)
    assert principal.principal_id == "p-1"
    # verify_access_jwt namespaces JWT-authenticated subjects with a `jwt:` prefix.
    assert principal.subject == "jwt:local:alice@x.com"
    assert principal.scopes == frozenset({"read"})


def test_mint_preserves_the_users_write_scope(jwt_env):
    token = mint_forwardable_api_jwt("p-2", "local:admin@x.com", frozenset({"read", "write"}))
    principal = verify_access_jwt(token, secret=_SECRET, issuer="doc-ray", audience=None)
    assert principal.scopes == frozenset({"read", "write"})


def test_mint_honours_configured_audience(monkeypatch):
    monkeypatch.setenv("DOCRAY_API_JWT_SECRET", _SECRET)
    monkeypatch.setenv("DOCRAY_API_JWT_ISSUER", "doc-ray")
    monkeypatch.setenv("DOCRAY_API_JWT_AUDIENCE", "docray-api")
    token = mint_forwardable_api_jwt("p-3", "local:u@x.com", frozenset({"read"}))
    # Verifies only when the audience matches; a mismatch is rejected.
    assert verify_access_jwt(token, secret=_SECRET, issuer="doc-ray", audience="docray-api")
    with pytest.raises(ValueError):
        verify_access_jwt(token, secret=_SECRET, issuer="doc-ray", audience="other")


def test_mint_returns_none_without_a_secret_so_login_degrades_gracefully(monkeypatch):
    monkeypatch.delenv("DOCRAY_API_JWT_SECRET", raising=False)
    assert mint_forwardable_api_jwt("p", "s", frozenset({"read"})) is None


def test_complete_local_login_attaches_a_forwardable_bearer(jwt_env, monkeypatch):
    class _FakeService:
        def login(self, *, email, password):
            return LocalPrincipal(
                subject="local:alice@x.com", principal_id="p-77", scopes=frozenset({"read"})
            )

    monkeypatch.setattr(auth_web, "_local_auth_service", lambda: _FakeService())
    result = auth_web.complete_local_login("alice@x.com", "pw")
    assert result.principal_id == "p-77"
    assert result.bearer_token is not None
    principal = verify_access_jwt(
        result.bearer_token, secret=_SECRET, issuer="doc-ray", audience=None
    )
    assert principal.principal_id == "p-77"
    assert principal.scopes == frozenset({"read"})


def test_minted_bearer_is_forwarded_by_api_auth_headers(jwt_env, monkeypatch):
    # End-to-end tie to the fixed bug: a local session's minted token is what the
    # page-image/original proxy sends — no static token, no act-as impersonation.
    monkeypatch.setattr(graphql_client, "API_TOKEN", "service-token")  # must NOT win
    minted = mint_forwardable_api_jwt("p-88", "local:u@x.com", frozenset({"read"}))
    auth_sessions.reset()
    sid = auth_sessions.create(principal_id="p-88", subject="local:u@x.com", bearer_token=minted)
    with webapp.app.test_request_context("/"):
        session["_auth_sid"] = sid
        headers = graphql_client.api_auth_headers()
    assert headers["Authorization"] == f"Bearer {minted}"
    assert "X-DocRay-Act-As" not in headers
