"""HTTP-level tests for the webapp OAuth 2.1 Authorization Server endpoints."""

from __future__ import annotations

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
    assert body["scopes_supported"] == ["read"]
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
