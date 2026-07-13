"""API routes for local self-service registration and login."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx

import doc_ray.entrypoints.api.app as api_app
from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.security import HeaderTokenAuthenticator, Principal


class _ClosedAuditPool:
    closed = True
    _opened = False

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _FakeLocalAuthService:
    def __init__(self) -> None:
        self.principal_id = str(uuid4())
        self.verified = False
        self.registered = False

    def register(self, *, email: str, password: str, display_name: str | None = None):
        self.registered = True

        class Result:
            principal_id = self.principal_id
            email_normalized = email.lower()
            verification_token = "verify-token"

        return Result()

    def verify_email(self, token: str):
        assert token == "verify-token"
        self.verified = True

        class Result:
            principal_id = self.principal_id
            email_normalized = "alice@example.com"

        return Result()

    def login(self, *, email: str, password: str) -> Principal:
        if not self.verified:
            from doc_ray.application.local_auth import EmailNotVerified

            raise EmailNotVerified("email is not verified")
        return Principal(
            subject=f"local:{email.lower()}",
            principal_id=self.principal_id,
            scopes=frozenset({"read"}),
        )


async def _post(app: object, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(path, **kwargs)


def _cfg() -> Config:
    return Config(
        database_url="postgresql://example",
        s3_endpoint="http://localhost:9000",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        s3_bucket="documents",
        s3_region="us-east-1",
        api_allow_anonymous=False,
        api_jwt_secret="jwt-signing-secret",
        api_jwt_issuer="doc-ray",
        api_jwt_audience="docray-api",
        api_jwt_ttl_seconds=300,
        auth_local_enabled=True,
        auth_registration_enabled=True,
        auth_expose_verification_token=True,
    )


class _ApiRecordingMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


class _ResendCapableService(_FakeLocalAuthService):
    def __init__(self, fresh_token: str | None) -> None:
        super().__init__()
        self.fresh_token = fresh_token
        self.resend_calls: list[str] = []

    def resend_verification(self, *, email: str) -> str | None:
        self.resend_calls.append(email)
        return self.fresh_token


def _cfg_production() -> Config:
    """Like _cfg() but with the dev token-exposure flag OFF (production shape)."""
    import dataclasses

    return dataclasses.replace(_cfg(), auth_expose_verification_token=False)


def test_api_register_fails_closed_without_verification_delivery(monkeypatch) -> None:
    """Regression for the audit blocker: no mailer + no dev exposure means the
    token would be silently discarded — the API must refuse with 503 and must
    not create the account."""
    monkeypatch.setattr(api_app, "AsyncConnectionPool", lambda *args, **kwargs: _ClosedAuditPool())
    app = create_app(_cfg_production())
    service = _FakeLocalAuthService()
    app.state.local_auth_service = service
    app.state.verification_mailer = None

    resp = asyncio.run(
        _post(
            app,
            "/auth/register",
            json={"email": "alice@example.com", "password": "correct horse battery staple"},
        )
    )

    assert resp.status_code == 503
    assert "delivery" in resp.json()["detail"].lower()
    assert service.registered is False


def test_api_register_emails_the_verification_link(monkeypatch) -> None:
    monkeypatch.setattr(api_app, "AsyncConnectionPool", lambda *args, **kwargs: _ClosedAuditPool())
    monkeypatch.setenv("DOCRAY_PUBLIC_BASE_URL", "https://docs.example.com")
    app = create_app(_cfg_production())
    app.state.local_auth_service = _FakeLocalAuthService()
    mailer = _ApiRecordingMailer()
    app.state.verification_mailer = mailer

    resp = asyncio.run(
        _post(
            app,
            "/auth/register",
            json={"email": "Alice@Example.com", "password": "correct horse battery staple"},
        )
    )

    assert resp.status_code == 202
    assert "verification_token" not in resp.json()
    assert len(mailer.sent) == 1
    to, _subject, body = mailer.sent[0]
    assert to == "alice@example.com"
    assert "https://docs.example.com/verify-email?token=verify-token" in body


def test_api_resend_verification_is_generic_and_sends_when_pending(monkeypatch) -> None:
    monkeypatch.setattr(api_app, "AsyncConnectionPool", lambda *args, **kwargs: _ClosedAuditPool())
    monkeypatch.setenv("DOCRAY_PUBLIC_BASE_URL", "https://docs.example.com")
    app = create_app(_cfg_production())
    pending = _ResendCapableService(fresh_token="fresh-token")
    app.state.local_auth_service = pending
    mailer = _ApiRecordingMailer()
    app.state.verification_mailer = mailer

    accepted = asyncio.run(
        _post(app, "/auth/resend-verification", json={"email": "alice@example.com"})
    )

    app.state.local_auth_service = _ResendCapableService(fresh_token=None)
    unknown = asyncio.run(
        _post(app, "/auth/resend-verification", json={"email": "nobody@example.com"})
    )

    assert accepted.status_code == 202
    assert unknown.status_code == 202
    assert accepted.json() == unknown.json()
    assert len(mailer.sent) == 1
    assert "token=fresh-token" in mailer.sent[0][2]


def test_local_register_requires_email_verification_before_login(monkeypatch) -> None:
    monkeypatch.setattr(api_app, "AsyncConnectionPool", lambda *args, **kwargs: _ClosedAuditPool())
    app = create_app(_cfg())
    service = _FakeLocalAuthService()
    app.state.local_auth_service = service

    registered = asyncio.run(
        _post(
            app,
            "/auth/register",
            json={
                "email": "Alice@Example.com",
                "password": "correct horse battery staple",
                "display_name": "Alice",
            },
        )
    )
    blocked = asyncio.run(
        _post(
            app,
            "/auth/login",
            json={"email": "alice@example.com", "password": "correct horse battery staple"},
        )
    )

    assert registered.status_code == 202
    assert registered.json()["email_verification_required"] is True
    assert registered.json()["verification_token"] == "verify-token"
    assert blocked.status_code == 403


def test_local_verify_then_login_returns_rls_bound_access_token(monkeypatch) -> None:
    monkeypatch.setattr(api_app, "AsyncConnectionPool", lambda *args, **kwargs: _ClosedAuditPool())
    app = create_app(_cfg())
    service = _FakeLocalAuthService()
    app.state.local_auth_service = service
    asyncio.run(
        _post(
            app,
            "/auth/register",
            json={
                "email": "alice@example.com",
                "password": "correct horse battery staple",
            },
        )
    )

    verified = asyncio.run(_post(app, "/auth/verify-email", json={"token": "verify-token"}))
    logged_in = asyncio.run(
        _post(
            app,
            "/auth/login",
            json={"email": "alice@example.com", "password": "correct horse battery staple"},
        )
    )

    assert verified.status_code == 200
    assert logged_in.status_code == 200
    body = logged_in.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 300
    assert isinstance(body["access_token"], str)
    assert isinstance(body["refresh_token"], str)

    authenticator = HeaderTokenAuthenticator(
        tokens={},
        allow_anonymous=False,
        jwt_secret="jwt-signing-secret",
        jwt_issuer="doc-ray",
        jwt_audience="docray-api",
    )

    class Request:
        headers = {"authorization": f"Bearer {body['access_token']}"}

    async def check() -> None:
        principal = await authenticator.authenticate(Request())  # type: ignore[arg-type]
        assert principal.principal_id == service.principal_id
        assert principal.scopes == frozenset({"read"})
        assert principal.subject == "jwt:local:alice@example.com"

    asyncio.run(check())
