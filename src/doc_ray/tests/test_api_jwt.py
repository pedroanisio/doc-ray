"""Short-lived signed API access-token tests (BL-071)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

import doc_ray.entrypoints.api.app as api_app
from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.security import (
    HeaderTokenAuthenticator,
    Principal,
    issue_access_jwt,
)


class _Request:
    def __init__(self, token: str) -> None:
        self.headers = {"authorization": f"Bearer {token}"}


class _ClosedAuditPool:
    closed = True
    _opened = False

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _disable_route_db_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_app, "AsyncConnectionPool", lambda *args, **kwargs: _ClosedAuditPool())


async def _post(app: object, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(path, **kwargs)


def test_config_reads_api_jwt_settings(monkeypatch):
    monkeypatch.setenv("DOCRAY_API_JWT_SECRET", "jwt-signing-secret")
    monkeypatch.setenv("DOCRAY_API_JWT_ISSUER", "https://issuer.example")
    monkeypatch.setenv("DOCRAY_API_JWT_AUDIENCE", "docray-api")
    monkeypatch.setenv("DOCRAY_API_JWT_TTL_SECONDS", "600")

    cfg = Config.from_env()

    assert cfg.api_jwt_secret == "jwt-signing-secret"
    assert cfg.api_jwt_issuer == "https://issuer.example"
    assert cfg.api_jwt_audience == "docray-api"
    assert cfg.api_jwt_ttl_seconds == 600


def test_header_token_authenticator_accepts_signed_access_jwt():
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    principal_id = str(uuid4())
    token = issue_access_jwt(
        Principal(
            subject="oidc:user-123",
            principal_id=principal_id,
            scopes=frozenset({"read", "write"}),
        ),
        secret="jwt-signing-secret",
        issuer="doc-ray",
        audience="docray-api",
        ttl_seconds=900,
        now=now,
    )
    authenticator = HeaderTokenAuthenticator(
        tokens={},
        allow_anonymous=False,
        jwt_secret="jwt-signing-secret",
        jwt_issuer="doc-ray",
        jwt_audience="docray-api",
    )

    async def check() -> None:
        principal = await authenticator.authenticate(_Request(token))  # type: ignore[arg-type]
        assert principal.subject == "jwt:oidc:user-123"
        assert principal.principal_id == principal_id
        assert principal.scopes == frozenset({"read", "write"})

    asyncio.run(check())


def test_header_token_authenticator_rejects_expired_access_jwt():
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    token = issue_access_jwt(
        Principal(subject="oidc:user-123", principal_id=str(uuid4()), scopes=frozenset({"read"})),
        secret="jwt-signing-secret",
        issuer="doc-ray",
        audience=None,
        ttl_seconds=60,
        now=now,
    )
    authenticator = HeaderTokenAuthenticator(
        tokens={},
        allow_anonymous=False,
        jwt_secret="jwt-signing-secret",
        jwt_issuer="doc-ray",
        now=lambda: now + timedelta(seconds=61),
    )

    async def check() -> None:
        with pytest.raises(HTTPException) as exc:
            await authenticator.authenticate(_Request(token))  # type: ignore[arg-type]
        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid API token"

    asyncio.run(check())


def test_header_token_authenticator_rejects_tampered_access_jwt():
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    token = issue_access_jwt(
        Principal(subject="oidc:user-123", principal_id=str(uuid4()), scopes=frozenset({"read"})),
        secret="jwt-signing-secret",
        issuer="doc-ray",
        audience=None,
        ttl_seconds=60,
        now=now,
    )
    tampered = token.rsplit(".", 1)[0] + ".not-the-signature"
    authenticator = HeaderTokenAuthenticator(
        tokens={},
        allow_anonymous=False,
        jwt_secret="jwt-signing-secret",
        jwt_issuer="doc-ray",
        now=lambda: now,
    )

    async def check() -> None:
        with pytest.raises(HTTPException) as exc:
            await authenticator.authenticate(_Request(tampered))  # type: ignore[arg-type]
        assert exc.value.status_code == 401

    asyncio.run(check())


def test_header_token_authenticator_keeps_static_token_fallback_with_jwt_enabled():
    static_token = "break-glass-token"
    principal_id = str(uuid4())
    authenticator = HeaderTokenAuthenticator(
        tokens={static_token: (principal_id, frozenset({"read"}))},
        allow_anonymous=False,
        jwt_secret="jwt-signing-secret",
        jwt_issuer="doc-ray",
    )

    async def check() -> None:
        principal = await authenticator.authenticate(_Request(static_token))  # type: ignore[arg-type]
        assert principal.subject == "token:break-glass-token"
        assert principal.principal_id == principal_id
        assert principal.scopes == frozenset({"read"})

    asyncio.run(check())


def test_api_issues_access_jwt_from_authenticated_static_token(monkeypatch):
    _disable_route_db_pool(monkeypatch)
    principal_id = str(uuid4())
    app = create_app(
        Config(
            database_url="postgresql://example",
            s3_endpoint="http://localhost:9000",
            s3_access_key="minioadmin",
            s3_secret_key="minioadmin",
            s3_bucket="documents",
            s3_region="us-east-1",
            api_tokens={"static-token": (principal_id, frozenset({"read", "write"}))},
            api_allow_anonymous=False,
            api_jwt_secret="jwt-signing-secret",
            api_jwt_issuer="doc-ray",
            api_jwt_audience="docray-api",
            api_jwt_ttl_seconds=300,
        )
    )

    response = asyncio.run(
        _post(app, "/auth/token", headers={"authorization": "Bearer static-token"})
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 300
    assert isinstance(body["access_token"], str)

    authenticator = HeaderTokenAuthenticator(
        tokens={},
        allow_anonymous=False,
        jwt_secret="jwt-signing-secret",
        jwt_issuer="doc-ray",
        jwt_audience="docray-api",
    )

    async def check() -> None:
        principal = await authenticator.authenticate(  # type: ignore[arg-type]
            _Request(body["access_token"])
        )
        assert principal.subject == "jwt:token:static-token"
        assert principal.principal_id == principal_id
        assert principal.scopes == frozenset({"read", "write"})

    asyncio.run(check())
