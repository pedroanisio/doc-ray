"""HTTP-level tests for the webapp OAuth 2.1 Authorization Server endpoints."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import replace

import pytest

# DATABASE_URL is needed at import (app construction); respect a real one if set.
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/none")

from doc_ray.application.oauth_server import OAuthServerService, sha256_hex  # noqa: E402
from webapp import app as app_mod  # noqa: E402
from webapp import oauth_web  # noqa: E402


class _FakeClientStore:
    def __init__(self):
        self.clients = {}
        self.secret_hashes = {}

    def create_client(self, client, *, secret_hash):
        self.clients[client.client_id] = client
        self.secret_hashes[client.client_id] = secret_hash

    def get_client(self, client_id):
        c = self.clients.get(client_id)
        return replace(c, client_secret=None) if c else None

    def verify_client_secret(self, client_id, secret):
        h = self.secret_hashes.get(client_id)
        return h is not None and sha256_hex(secret) == h


class _FakeCodeStore:
    def __init__(self):
        self.codes = {}

    def store_code(self, *, code_hash, record):
        self.codes[code_hash] = dict(record, consumed_at=None)

    def consume_code(self, *, code_hash, now):
        r = self.codes.get(code_hash)
        if r is None or r["consumed_at"] is not None:
            return None
        r["consumed_at"] = now
        return None if r["expires_at"] <= now else r


@pytest.fixture
def svc(monkeypatch):
    # Scoped to the test (auto-undone) so it can't leak into other suites.
    monkeypatch.setenv("DOCRAY_PUBLIC_BASE_URL", "https://doc-ray.com")
    service = OAuthServerService(
        _FakeClientStore(),
        _FakeCodeStore(),
        client_id_factory=lambda: "cid-test",
        code_factory=lambda: "code-test",
    )
    monkeypatch.setattr(oauth_web, "_SERVICE", service)
    return service


@pytest.fixture
def client():
    return app_mod.app.test_client()


def test_protected_resource_metadata(client, svc):
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["resource"] == "https://doc-ray.com/mcp"
    assert body["authorization_servers"] == ["https://doc-ray.com"]
    assert body["scopes_supported"] == ["read", "write"]
    # Path-scoped variant resolves to the same metadata.
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 200


def test_authorization_server_metadata(client, svc):
    body = client.get("/.well-known/oauth-authorization-server").get_json()
    assert body["issuer"] == "https://doc-ray.com"
    assert body["authorization_endpoint"] == "https://doc-ray.com/oauth/authorize"
    assert body["token_endpoint"] == "https://doc-ray.com/oauth/token"
    assert body["registration_endpoint"] == "https://doc-ray.com/oauth/register"
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert "authorization_code" in body["grant_types_supported"]
    assert "client_credentials" in body["grant_types_supported"]


def test_dynamic_client_registration_needs_no_csrf(client, svc):
    resp = client.post(
        "/oauth/register",
        json={"redirect_uris": ["https://claude.ai/cb"], "client_name": "Claude"},
    )
    assert resp.status_code == 201, resp.data
    body = resp.get_json()
    assert body["client_id"] == "cid-test"
    assert body["token_endpoint_auth_method"] == "none"
    assert body["redirect_uris"] == ["https://claude.ai/cb"]


def test_register_rejects_bad_redirect(client, svc):
    resp = client.post("/oauth/register", json={"redirect_uris": ["http://evil.example.com/cb"]})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_redirect_uri"


def test_authorize_redirects_anonymous_to_login(client, svc):
    svc.register_client({"redirect_uris": ["https://claude.ai/cb"], "client_name": "Claude"})
    resp = client.get(
        "/oauth/authorize",
        query_string={
            "client_id": "cid-test",
            "redirect_uri": "https://claude.ai/cb",
            "response_type": "code",
            "code_challenge": "abc123",
            "code_challenge_method": "S256",
            "state": "s1",
            "resource": "https://doc-ray.com/mcp",
        },
    )
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


class _Record:
    def __init__(self, principal_id, subject):
        self.principal_id = principal_id
        self.subject = subject


class _FakeLocalAuth:
    def __init__(self, scopes):
        self._scopes = scopes

    def scopes_for_principal(self, principal_id):
        return self._scopes


def _login_as(monkeypatch, *, principal_id="p-1", subject="local:ada@example.com", scopes):
    """Mock a logged-in web session whose RBAC yields *scopes*."""
    monkeypatch.setattr(
        oauth_web.auth_web, "_current_auth_session", lambda: _Record(principal_id, subject)
    )
    monkeypatch.setattr(
        oauth_web.auth_web, "_local_auth_service", lambda: _FakeLocalAuth(frozenset(scopes))
    )


def _approve(client, *, extra=None):
    client.set_cookie("session", "x")  # ensure a session exists for CSRF
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "csrf-1"
    form = {
        "csrf_token": "csrf-1",
        "client_id": "cid-test",
        "redirect_uri": "https://claude.ai/cb",
        "response_type": "code",
        "code_challenge": "challenge-abc",
        "code_challenge_method": "S256",
        "scope": "read write",
        "state": "s1",
        "resource": "https://doc-ray.com/mcp",
        "decision": "approve",
    }
    form.update(extra or {})
    return client.post("/oauth/authorize", data=form)


def _stored_scope(svc):
    (record,) = list(svc._codes.codes.values())
    return record["scope"]


def test_authorize_submit_grants_write_to_a_write_capable_user(client, svc, monkeypatch):
    svc.register_client({"redirect_uris": ["https://claude.ai/cb"], "client_name": "Claude"})
    _login_as(monkeypatch, scopes={"read", "write"})
    resp = _approve(client)
    assert resp.status_code == 302 and "code=" in resp.headers["Location"]
    assert _stored_scope(svc) == "read write"  # RBAC carries write -> granted


def test_authorize_submit_bounds_a_reader_to_read(client, svc, monkeypatch):
    svc.register_client({"redirect_uris": ["https://claude.ai/cb"], "client_name": "Claude"})
    _login_as(monkeypatch, scopes={"read"})
    resp = _approve(client)
    assert resp.status_code == 302 and "code=" in resp.headers["Location"]
    assert _stored_scope(svc) == "read"  # no write in RBAC -> read-only even though requested


def test_consent_screen_shows_write_for_a_write_capable_user(client, svc, monkeypatch):
    svc.register_client({"redirect_uris": ["https://claude.ai/cb"], "client_name": "Claude"})
    _login_as(monkeypatch, scopes={"read", "write"})
    resp = client.get(
        "/oauth/authorize",
        query_string={
            "client_id": "cid-test",
            "redirect_uri": "https://claude.ai/cb",
            "response_type": "code",
            "code_challenge": "challenge-abc",
            "code_challenge_method": "S256",
            "state": "s1",
            "resource": "https://doc-ray.com/mcp",
        },
    )
    assert resp.status_code == 200
    assert b"write" in resp.data


def test_authorize_unknown_client_is_error_not_redirect(client, svc):
    resp = client.get(
        "/oauth/authorize",
        query_string={"client_id": "nope", "redirect_uri": "https://claude.ai/cb"},
    )
    # Unknown client is non-redirectable: must not 302 to the (untrusted) redirect.
    assert resp.status_code == 400
    assert "claude.ai" not in resp.headers.get("Location", "")


def test_token_unsupported_grant(client, svc, monkeypatch):
    monkeypatch.setenv("DOCRAY_API_JWT_SECRET", "test-secret-value")
    resp = client.post("/oauth/token", data={"grant_type": "password"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unsupported_grant_type"


def _decode_claims(jwt: str) -> dict:
    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def test_client_credentials_mints_resource_bound_token(client, svc, monkeypatch):
    monkeypatch.setenv("DOCRAY_API_JWT_SECRET", "test-secret-value")
    # Register a confidential client, then provision a service principal on it.
    c = svc.register_client(
        {"redirect_uris": ["https://x/cb"], "token_endpoint_auth_method": "client_secret_post"}
    )
    pid = "11111111-1111-1111-1111-111111111111"
    svc._clients.clients[c.client_id] = replace(svc._clients.clients[c.client_id], principal_id=pid)

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": c.client_id,
            "client_secret": c.client_secret,
            "resource": "https://doc-ray.com/mcp",
        },
    )
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["access_token"] and "refresh_token" not in body  # 2.1: no refresh here
    claims = _decode_claims(body["access_token"])
    assert claims["aud"] == "https://doc-ray.com/mcp"  # RFC 8707 audience binding
    assert claims["principal_id"] == pid


def test_client_credentials_refused_for_unprovisioned_client(client, svc, monkeypatch):
    monkeypatch.setenv("DOCRAY_API_JWT_SECRET", "test-secret-value")
    c = svc.register_client(
        {"redirect_uris": ["https://x/cb"], "token_endpoint_auth_method": "client_secret_post"}
    )
    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": c.client_id,
            "client_secret": c.client_secret,
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unauthorized_client"
