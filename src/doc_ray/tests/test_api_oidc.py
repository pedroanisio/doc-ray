"""OIDC bearer-token authentication tests (BL-066)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from fastapi import HTTPException

from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.oidc import (
    OIDCAuthenticator,
    OIDCPrincipalPool,
    PostgresOIDCPrincipalResolver,
)
from doc_ray.entrypoints.api.security import HeaderTokenAuthenticator, Principal

_ROOT = Path(__file__).resolve().parents[3]


class _Request:
    def __init__(self, token: str | None) -> None:
        self.headers = {"authorization": f"Bearer {token}"} if token else {}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64url_json(value: Mapping[str, object]) -> str:
    return _b64url(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())


def _hs256_jwt(
    *,
    secret: str,
    kid: str,
    issuer: str = "https://issuer.example",
    audience: str = "docray-api",
    subject: str = "user-123",
    now: datetime,
) -> str:
    header = {"alg": "HS256", "kid": kid, "typ": "JWT"}
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    signing_input = f"{_b64url_json(header)}.{_b64url_json(claims)}"
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def _oct_jwks(secret: str, kid: str = "oidc-test-key") -> dict[str, object]:
    return {"keys": [{"kty": "oct", "kid": kid, "alg": "HS256", "k": _b64url(secret.encode())}]}


def test_config_reads_oidc_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCRAY_API_OIDC_ISSUER", "https://issuer.example")
    monkeypatch.setenv("DOCRAY_API_OIDC_AUDIENCE", "docray-api")
    monkeypatch.setenv("DOCRAY_API_OIDC_JWKS_JSON", json.dumps(_oct_jwks("oidc-secret")))
    monkeypatch.setenv("DOCRAY_API_OIDC_AUTO_CREATE_PRINCIPALS", "true")

    cfg = Config.from_env()

    assert cfg.api_oidc_issuer == "https://issuer.example"
    assert cfg.api_oidc_audience == "docray-api"
    assert json.loads(cfg.api_oidc_jwks_json or "{}")["keys"][0]["kid"] == "oidc-test-key"
    assert cfg.api_oidc_auto_create_principals is True
    assert cfg.api_oidc_allow_static_token_fallback is False


def test_config_reads_oidc_static_token_fallback_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCRAY_API_OIDC_ALLOW_STATIC_TOKEN_FALLBACK", "true")

    assert Config.from_env().api_oidc_allow_static_token_fallback is True


def test_env_example_documents_static_token_fallback_precedence() -> None:
    text = (_ROOT / ".env.example").read_text(encoding="utf-8")
    lowered = text.lower()

    assert "DOCRAY_API_OIDC_ALLOW_STATIC_TOKEN_FALLBACK" in text
    assert "break-glass" in lowered
    assert "oidc" in lowered and "static" in lowered


def test_oidc_authenticator_validates_jwks_jwt_and_resolves_external_subject() -> None:
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    principal_id = str(uuid4())
    token = _hs256_jwt(secret="oidc-secret", kid="oidc-test-key", now=now)

    async def resolve(subject: str, claims: dict[str, object]) -> Principal | None:
        assert subject == "user-123"
        assert claims["sub"] == "user-123"
        return Principal(subject=f"oidc:{subject}", principal_id=principal_id, scopes=frozenset({"read"}))

    authenticator = OIDCAuthenticator(
        issuer="https://issuer.example",
        audience="docray-api",
        jwks=_oct_jwks("oidc-secret"),
        resolve_principal=resolve,
        fallback=HeaderTokenAuthenticator(tokens={}, allow_anonymous=False),
        now=lambda: now,
    )

    async def check() -> None:
        principal = await authenticator.authenticate(_Request(token))  # type: ignore[arg-type]
        assert principal.subject == "oidc:user-123"
        assert principal.principal_id == principal_id
        assert principal.scopes == frozenset({"read"})

    asyncio.run(check())


def test_oidc_authenticator_rejects_wrong_issuer() -> None:
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    token = _hs256_jwt(
        secret="oidc-secret",
        kid="oidc-test-key",
        issuer="https://evil.example",
        now=now,
    )
    authenticator = OIDCAuthenticator(
        issuer="https://issuer.example",
        audience="docray-api",
        jwks=_oct_jwks("oidc-secret"),
        resolve_principal=lambda _subject, _claims: None,
        fallback=HeaderTokenAuthenticator(tokens={}, allow_anonymous=False),
        now=lambda: now,
    )

    async def check() -> None:
        with pytest.raises(HTTPException) as exc:
            await authenticator.authenticate(_Request(token))  # type: ignore[arg-type]
        assert exc.value.status_code == 401

    asyncio.run(check())


def test_oidc_authenticator_keeps_static_token_break_glass_fallback() -> None:
    static_principal = str(uuid4())
    authenticator = OIDCAuthenticator(
        issuer="https://issuer.example",
        audience="docray-api",
        jwks=_oct_jwks("oidc-secret"),
        resolve_principal=lambda _subject, _claims: None,
        fallback=HeaderTokenAuthenticator(
            tokens={"break-glass": (static_principal, frozenset({"read", "write"}))},
            allow_anonymous=False,
        ),
        now=lambda: datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
    )

    async def check() -> None:
        principal = await authenticator.authenticate(_Request("break-glass"))  # type: ignore[arg-type]
        assert principal.subject == "token:break-glass"
        assert principal.principal_id == static_principal
        assert principal.scopes == frozenset({"read", "write"})

    asyncio.run(check())


def test_create_app_uses_oidc_authenticator_when_issuer_and_jwks_are_configured() -> None:
    cfg = Config(
        database_url="postgresql://example",
        s3_endpoint="http://localhost:9000",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        s3_bucket="documents",
        s3_region="us-east-1",
        api_oidc_issuer="https://issuer.example",
        api_oidc_audience="docray-api",
        api_oidc_jwks_json=json.dumps(_oct_jwks("oidc-secret")),
        api_tokens={"break-glass": (str(uuid4()), frozenset({"read"}))},
        api_allow_anonymous=False,
    )

    app = create_app(cfg)

    assert isinstance(app.state.authenticator, OIDCAuthenticator)


def test_create_app_oidc_rejects_static_tokens_without_explicit_fallback() -> None:
    cfg = Config(
        database_url="postgresql://example",
        s3_endpoint="http://localhost:9000",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        s3_bucket="documents",
        s3_region="us-east-1",
        api_oidc_issuer="https://issuer.example",
        api_oidc_audience="docray-api",
        api_oidc_jwks_json=json.dumps(_oct_jwks("oidc-secret")),
        api_tokens={"break-glass": (str(uuid4()), frozenset({"read"}))},
        api_allow_anonymous=False,
    )
    app = create_app(cfg)

    async def check() -> None:
        with pytest.raises(HTTPException) as exc:
            await app.state.authenticator.authenticate(_Request("break-glass"))
        assert exc.value.status_code == 401

    asyncio.run(check())


def test_create_app_oidc_accepts_static_tokens_when_fallback_is_explicitly_enabled() -> None:
    principal_id = str(uuid4())
    cfg = Config(
        database_url="postgresql://example",
        s3_endpoint="http://localhost:9000",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        s3_bucket="documents",
        s3_region="us-east-1",
        api_oidc_issuer="https://issuer.example",
        api_oidc_audience="docray-api",
        api_oidc_jwks_json=json.dumps(_oct_jwks("oidc-secret")),
        api_oidc_allow_static_token_fallback=True,
        api_tokens={"break-glass": (principal_id, frozenset({"read"}))},
        api_allow_anonymous=False,
    )
    app = create_app(cfg)

    async def check() -> None:
        principal = await app.state.authenticator.authenticate(_Request("break-glass"))
        assert principal.subject == "token:break-glass"
        assert principal.principal_id == principal_id

    asyncio.run(check())


def test_postgres_oidc_resolver_can_populate_external_subject_when_policy_allows() -> None:
    principal_id = str(uuid4())
    calls: list[tuple[str, tuple[object, ...]]] = []

    class _Cursor:
        def __init__(self, row: dict[str, object] | None) -> None:
            self._row = row

        async def fetchone(self) -> dict[str, object] | None:
            return self._row

    class _Conn:
        async def execute(self, query: str, params: tuple[object, ...]) -> _Cursor:
            calls.append((query, params))
            if query.startswith("SELECT"):
                return _Cursor(None)
            return _Cursor({"id": principal_id})

    class _Context:
        async def __aenter__(self) -> _Conn:
            return _Conn()

        async def __aexit__(self, *args: object) -> None:
            return None

    class _Pool:
        def connection(self) -> _Context:
            return _Context()

    resolver = PostgresOIDCPrincipalResolver(cast(OIDCPrincipalPool, _Pool()), auto_create=True)

    async def check() -> None:
        principal = await resolver("user-123", {"email": "user@example.test"})
        assert principal is not None
        assert principal.subject == "oidc:user-123"
        assert principal.principal_id == principal_id
        assert calls[1][1] == ("user@example.test", "user-123")

    asyncio.run(check())
