"""Unit tests for the OAuth 2.1 authorization-server domain logic."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from doc_ray.application.oauth_server import (
    OAuthError,
    OAuthServerService,
    sha256_hex,
)

_T0 = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class _FakeClientStore:
    def __init__(self) -> None:
        self.clients: dict[str, Any] = {}
        self.secret_hashes: dict[str, str | None] = {}

    def create_client(self, client, *, secret_hash):
        self.clients[client.client_id] = client
        self.secret_hashes[client.client_id] = secret_hash

    def get_client(self, client_id):
        client = self.clients.get(client_id)
        return replace(client, client_secret=None) if client is not None else None

    def verify_client_secret(self, client_id, secret):
        stored = self.secret_hashes.get(client_id)
        return stored is not None and secrets.compare_digest(sha256_hex(secret), stored)


class _FakeCodeStore:
    def __init__(self) -> None:
        self.codes: dict[str, dict[str, Any]] = {}

    def store_code(self, *, code_hash, record):
        self.codes[code_hash] = dict(record, consumed_at=None)

    def consume_code(self, *, code_hash, now):
        record = self.codes.get(code_hash)
        if record is None or record.get("consumed_at") is not None:
            return None
        record["consumed_at"] = now  # single-use: always burn
        if record["expires_at"] <= now:
            return None
        return record


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


def _service(clock: _Clock | None = None, ids=None, codes=None):
    id_iter = iter(ids or ["cid-1", "cid-2"])
    code_iter = iter(codes or ["code-1", "code-2"])
    return (
        OAuthServerService(
            _FakeClientStore(),
            _FakeCodeStore(),
            now=clock or _Clock(_T0),
            client_id_factory=lambda: next(id_iter),
            code_factory=lambda: next(code_iter),
        ),
    )[0]


# -- Dynamic Client Registration ---------------------------------------------


def test_register_public_client_returns_id_and_no_secret():
    svc = _service()
    client = svc.register_client(
        {"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"], "client_name": "Claude"}
    )
    assert client.client_id == "cid-1"
    assert client.token_endpoint_auth_method == "none"
    assert client.client_secret is None
    assert "authorization_code" in client.grant_types


def test_register_confidential_client_gets_secret():
    svc = _service()
    client = svc.register_client(
        {
            "redirect_uris": ["https://app.example.com/cb"],
            "token_endpoint_auth_method": "client_secret_post",
        }
    )
    assert client.client_secret is not None
    assert svc.get_client(client.client_id).client_secret is None  # never re-exposed


def test_register_requires_redirect_uris():
    with pytest.raises(OAuthError) as ei:
        _service().register_client({"client_name": "x"})
    assert ei.value.error == "invalid_client_metadata"


@pytest.mark.parametrize(
    "uri",
    ["http://evil.example.com/cb", "ftp://x/cb", "https://ok.example.com/cb#frag"],
)
def test_register_rejects_bad_redirect_uris(uri):
    with pytest.raises(OAuthError) as ei:
        _service().register_client({"redirect_uris": [uri]})
    assert ei.value.error == "invalid_redirect_uri"


def test_register_allows_loopback_http():
    svc = _service()
    client = svc.register_client({"redirect_uris": ["http://localhost:33418/callback"]})
    assert client.redirect_uris == ("http://localhost:33418/callback",)


# -- Authorization request validation ----------------------------------------


def _register(svc, redirect="https://claude.ai/cb"):
    return svc.register_client({"redirect_uris": [redirect], "client_name": "Claude"})


def test_authorize_unknown_client_is_invalid_client():
    svc = _service()
    with pytest.raises(OAuthError) as ei:
        svc.validate_authorization_request({"client_id": "nope", "redirect_uri": "https://x/cb"})
    assert ei.value.error == "invalid_client" and not ei.value.redirectable


def test_authorize_unregistered_redirect_is_not_redirectable():
    svc = _service()
    c = _register(svc)
    with pytest.raises(OAuthError) as ei:
        svc.validate_authorization_request(
            {"client_id": c.client_id, "redirect_uri": "https://attacker/cb"}
        )
    assert ei.value.error == "invalid_request" and not ei.value.redirectable


def test_authorize_requires_s256_pkce():
    svc = _service()
    c = _register(svc)
    base = {"client_id": c.client_id, "redirect_uri": c.redirect_uris[0], "response_type": "code"}
    with pytest.raises(OAuthError) as ei:  # missing challenge
        svc.validate_authorization_request(base)
    assert ei.value.error == "invalid_request" and ei.value.redirectable
    with pytest.raises(OAuthError) as ei2:  # plain not allowed
        svc.validate_authorization_request(
            {**base, "code_challenge": "abc", "code_challenge_method": "plain"}
        )
    assert ei2.value.redirectable


def test_authorize_valid_request_narrows_scope():
    svc = _service()
    c = _register(svc)
    _, challenge = _pkce()
    req = svc.validate_authorization_request(
        {
            "client_id": c.client_id,
            "redirect_uri": c.redirect_uris[0],
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "read write admin",
            "state": "xyz",
            "resource": "https://doc-ray.com/mcp",
        }
    )
    assert req.scope == "read write"  # 'admin' dropped; 'write' is now supported
    assert req.state == "xyz" and req.resource == "https://doc-ray.com/mcp"


# -- Scope grant is bounded by the consenting principal's RBAC ----------------


def test_supported_scopes_include_read_and_write():
    from doc_ray.application.oauth_server import SUPPORTED_SCOPES

    assert set(SUPPORTED_SCOPES) == {"read", "write"}


def _authorized_with(principal_scopes, requested="read write"):
    """Issue a code for a request, bounding the grant by *principal_scopes*."""
    svc = _service()
    client = _register(svc)
    verifier, challenge = _pkce()
    req = svc.validate_authorization_request(
        {
            "client_id": client.client_id,
            "redirect_uri": client.redirect_uris[0],
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": requested,
            "resource": "https://doc-ray.com/mcp",
        }
    )
    code = svc.issue_code(
        req,
        principal_id="prin-1",
        subject="local:ada@example.com",
        principal_scopes=principal_scopes,
    )
    grant = svc.exchange_authorization_code(
        {
            "client_id": client.client_id,
            "redirect_uri": client.redirect_uris[0],
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
        }
    )
    return grant


def test_write_capable_principal_gets_write_scope():
    grant = _authorized_with(frozenset({"read", "write"}))
    assert grant.scopes == frozenset({"read", "write"})


def test_read_only_principal_never_gets_write_even_if_requested():
    grant = _authorized_with(frozenset({"read"}), requested="read write")
    assert grant.scopes == frozenset({"read"})


def test_grant_is_capped_at_supported_scopes():
    # An over-broad principal capability is still bounded by SUPPORTED_SCOPES.
    grant = _authorized_with(frozenset({"read", "write", "admin"}))
    assert grant.scopes == frozenset({"read", "write"})


def test_issue_code_without_principal_scopes_preserves_request_scope():
    # Backward compatibility: callers that pass no RBAC context keep old behavior.
    svc = _service()
    client = _register(svc)
    verifier, challenge = _pkce()
    req = svc.validate_authorization_request(
        {
            "client_id": client.client_id,
            "redirect_uri": client.redirect_uris[0],
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "read write",
            "resource": "https://doc-ray.com/mcp",
        }
    )
    code = svc.issue_code(req, principal_id="p", subject="s")  # no principal_scopes
    grant = svc.exchange_authorization_code(
        {
            "client_id": client.client_id,
            "redirect_uri": client.redirect_uris[0],
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
        }
    )
    assert grant.scopes == frozenset({"read", "write"})  # == req.scope, unbounded


# -- Full code issue + PKCE redemption ---------------------------------------


def _authorized_service():
    svc = _service()
    client = _register(svc)
    verifier, challenge = _pkce()
    req = svc.validate_authorization_request(
        {
            "client_id": client.client_id,
            "redirect_uri": client.redirect_uris[0],
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": "https://doc-ray.com/mcp",
        }
    )
    code = svc.issue_code(req, principal_id="prin-1", subject="local:ada@example.com")
    return svc, client, verifier, code


def test_exchange_happy_path_returns_grant():
    svc, client, verifier, code = _authorized_service()
    grant = svc.exchange_authorization_code(
        {
            "client_id": client.client_id,
            "redirect_uri": client.redirect_uris[0],
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
        }
    )
    assert grant.principal_id == "prin-1"
    assert grant.subject == "local:ada@example.com"
    assert grant.scopes == frozenset({"read"})
    assert grant.resource == "https://doc-ray.com/mcp"


def test_exchange_wrong_verifier_fails():
    svc, client, _verifier, code = _authorized_service()
    with pytest.raises(OAuthError) as ei:
        svc.exchange_authorization_code(
            {
                "client_id": client.client_id,
                "redirect_uri": client.redirect_uris[0],
                "code": code,
                "code_verifier": "not-the-verifier",
            }
        )
    assert ei.value.error == "invalid_grant"


def test_exchange_code_is_single_use():
    svc, client, verifier, code = _authorized_service()
    params = {
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uris[0],
        "code": code,
        "code_verifier": verifier,
    }
    svc.exchange_authorization_code(params)  # first use ok
    with pytest.raises(OAuthError) as ei:
        svc.exchange_authorization_code(params)  # replay rejected
    assert ei.value.error == "invalid_grant"


def test_exchange_redirect_uri_must_match():
    svc, client, verifier, code = _authorized_service()
    with pytest.raises(OAuthError) as ei:
        svc.exchange_authorization_code(
            {
                "client_id": client.client_id,
                "redirect_uri": "https://claude.ai/OTHER",
                "code": code,
                "code_verifier": verifier,
            }
        )
    assert ei.value.error == "invalid_grant"


def test_exchange_expired_code_fails():
    clock = _Clock(_T0)
    svc = OAuthServerService(
        _FakeClientStore(),
        _FakeCodeStore(),
        now=clock,
        client_id_factory=lambda: "cid-1",
        code_factory=lambda: "code-1",
    )
    client = _register(svc)
    verifier, challenge = _pkce()
    req = svc.validate_authorization_request(
        {
            "client_id": client.client_id,
            "redirect_uri": client.redirect_uris[0],
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    code = svc.issue_code(req, principal_id="p", subject="s")
    clock.t = _T0 + timedelta(minutes=5)  # past the 60s TTL
    with pytest.raises(OAuthError) as ei:
        svc.exchange_authorization_code(
            {
                "client_id": client.client_id,
                "redirect_uri": client.redirect_uris[0],
                "code": code,
                "code_verifier": verifier,
            }
        )
    assert ei.value.error == "invalid_grant"


def test_exchange_confidential_client_requires_secret():
    svc = _service()
    client = svc.register_client(
        {
            "redirect_uris": ["https://app.example.com/cb"],
            "token_endpoint_auth_method": "client_secret_post",
        }
    )
    verifier, challenge = _pkce()
    req = svc.validate_authorization_request(
        {
            "client_id": client.client_id,
            "redirect_uri": client.redirect_uris[0],
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    code = svc.issue_code(req, principal_id="p", subject="s")
    base = {
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uris[0],
        "code": code,
        "code_verifier": verifier,
    }
    with pytest.raises(OAuthError) as ei:  # no secret
        svc.exchange_authorization_code(base)
    assert ei.value.error == "invalid_client"
    # correct secret succeeds
    grant = svc.exchange_authorization_code({**base, "client_secret": client.client_secret})
    assert grant.principal_id == "p"


# -- client_credentials grant ------------------------------------------------


def _confidential(repo, svc):
    return svc.register_client(
        {
            "redirect_uris": ["https://app.example.com/cb"],
            "token_endpoint_auth_method": "client_secret_post",
        }
    )


def test_client_credentials_rejects_public_client():
    repo = _FakeClientStore()
    svc = OAuthServerService(repo, _FakeCodeStore(), client_id_factory=lambda: "cid-pub")
    pub = svc.register_client(
        {"redirect_uris": ["https://x/cb"]}
    )  # token_endpoint_auth_method=none
    with pytest.raises(OAuthError) as ei:
        svc.exchange_client_credentials({"client_id": pub.client_id})
    assert ei.value.error == "invalid_client"


def test_client_credentials_refused_without_provisioned_principal():
    repo = _FakeClientStore()
    svc = OAuthServerService(repo, _FakeCodeStore(), client_id_factory=lambda: "cid-conf")
    c = _confidential(repo, svc)
    with pytest.raises(OAuthError) as ei:
        svc.exchange_client_credentials(
            {"client_id": c.client_id, "client_secret": c.client_secret}
        )
    # Confidential but no service principal bound -> cannot escalate.
    assert ei.value.error == "unauthorized_client"


def test_client_credentials_wrong_secret_rejected():
    repo = _FakeClientStore()
    svc = OAuthServerService(repo, _FakeCodeStore(), client_id_factory=lambda: "cid-svc")
    c = _confidential(repo, svc)
    repo.clients[c.client_id] = replace(repo.clients[c.client_id], principal_id="svc-principal")
    with pytest.raises(OAuthError) as ei:
        svc.exchange_client_credentials({"client_id": c.client_id, "client_secret": "wrong"})
    assert ei.value.error == "invalid_client"


def test_client_credentials_success_for_provisioned_service_client():
    repo = _FakeClientStore()
    svc = OAuthServerService(repo, _FakeCodeStore(), client_id_factory=lambda: "cid-svc")
    c = _confidential(repo, svc)
    # Operator provisions a service principal on the client (out-of-band).
    repo.clients[c.client_id] = replace(repo.clients[c.client_id], principal_id="svc-principal")
    grant = svc.exchange_client_credentials(
        {
            "client_id": c.client_id,
            "client_secret": c.client_secret,
            "resource": "https://doc-ray.com/mcp",
        }
    )
    assert grant.principal_id == "svc-principal"
    assert grant.subject == f"oauth-client:{c.client_id}"
    assert grant.scopes == frozenset({"read"})
    assert grant.resource == "https://doc-ray.com/mcp"
