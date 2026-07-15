"""API auth token routes: JWT issue/refresh and local-account endpoints.

Split out of :mod:`doc_ray.entrypoints.api.app` (D-015), following the
``register_saml_routes``/``register_scim_routes`` sibling precedent. Every
issue/rotate outcome is recorded to the append-only security audit log.
"""

from __future__ import annotations

import logging
import os
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request

from doc_ray.application.local_auth import (
    DisabledPrincipal,
    EmailAlreadyRegistered,
    EmailNotVerified,
    InvalidCredentials,
    LocalAuthError,
    LocalAuthService,
    VerificationTokenExpired,
    VerificationTokenInvalid,
)
from doc_ray.config import Config
from doc_ray.entrypoints.api.security import (
    Authenticator,
    Principal,
    issue_access_jwt,
    issue_refresh_token,
    rotate_refresh_token,
    verify_access_jwt,
)
from doc_ray.entrypoints.api.security_audit import (
    record_audit_event_from_pool,
    request_id_from_request,
    source_ip_from_request,
)
from doc_ray.ports.mailer import Mailer

_logger = logging.getLogger("doc_ray.api")


def _issue_jwt_pair(
    *,
    principal: Principal,
    request: Request,
    settings: Config,
) -> dict[str, Any]:
    """Mint a fresh access+refresh token pair for *principal*."""
    if not settings.api_jwt_secret:
        raise HTTPException(status_code=503, detail="JWT access tokens are not configured")
    access_token = issue_access_jwt(
        principal,
        secret=settings.api_jwt_secret,
        issuer=settings.api_jwt_issuer,
        audience=settings.api_jwt_audience,
        ttl_seconds=settings.api_jwt_ttl_seconds,
    )
    refresh_token = issue_refresh_token(
        principal,
        secret=settings.api_jwt_secret,
        issuer=settings.api_jwt_issuer,
        audience=settings.api_jwt_audience,
        ttl_seconds=settings.api_refresh_token_ttl_seconds,
        store=request.app.state.refresh_token_store,
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": settings.api_jwt_ttl_seconds,
        "refresh_expires_in": settings.api_refresh_token_ttl_seconds,
    }


def register_auth_token_route(app: FastAPI, settings: Config) -> None:
    """Attach the ``/auth/token`` and ``/auth/refresh`` routes to *app*."""

    async def audit_auth_event(
        request: Request,
        *,
        principal: Principal | None,
        action: str,
        outcome: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        await record_audit_event_from_pool(
            request.app.state.pool,
            principal=principal,
            action=action,
            target_type="auth",
            target_id=None,
            outcome=outcome,
            source_ip=source_ip_from_request(request),
            request_id=request_id_from_request(request),
            details=details,
        )

    @app.post("/auth/token")
    async def issue_api_access_token(request: Request) -> dict[str, Any]:
        """Exchange an authenticated API credential for a short-lived access JWT."""
        if not settings.api_jwt_secret:
            raise HTTPException(status_code=503, detail="JWT access tokens are not configured")
        authenticator: Authenticator = request.app.state.authenticator
        try:
            principal = await authenticator.authenticate(request)
        except HTTPException as exc:
            await audit_auth_event(
                request,
                principal=None,
                action="auth.token.issue",
                outcome="failure",
                details={"status_code": exc.status_code},
            )
            raise
        if principal.principal_id is None:
            await audit_auth_event(
                request,
                principal=principal,
                action="auth.token.issue",
                outcome="failure",
                details={"status_code": 401},
            )
            raise HTTPException(status_code=401, detail="authentication required")
        token = issue_access_jwt(
            principal,
            secret=settings.api_jwt_secret,
            issuer=settings.api_jwt_issuer,
            audience=settings.api_jwt_audience,
            ttl_seconds=settings.api_jwt_ttl_seconds,
        )
        refresh_token = issue_refresh_token(
            principal,
            secret=settings.api_jwt_secret,
            issuer=settings.api_jwt_issuer,
            audience=settings.api_jwt_audience,
            ttl_seconds=settings.api_refresh_token_ttl_seconds,
            store=request.app.state.refresh_token_store,
        )
        await audit_auth_event(
            request,
            principal=principal,
            action="auth.token.issue",
            outcome="success",
        )
        return {
            "access_token": token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": settings.api_jwt_ttl_seconds,
            "refresh_expires_in": settings.api_refresh_token_ttl_seconds,
        }

    @app.post("/auth/refresh")
    async def refresh_api_access_token(payload: dict[str, str], request: Request) -> dict[str, Any]:
        """Rotate a refresh token and return a fresh access+refresh pair."""
        if not settings.api_jwt_secret:
            raise HTTPException(status_code=503, detail="JWT access tokens are not configured")
        refresh_token = str(payload.get("refresh_token", "")).strip()
        if not refresh_token:
            await audit_auth_event(
                request,
                principal=None,
                action="auth.refresh.rotate",
                outcome="failure",
                details={"status_code": 400},
            )
            raise HTTPException(status_code=400, detail="refresh_token is required")
        try:
            access_token, next_refresh_token = rotate_refresh_token(
                refresh_token,
                secret=settings.api_jwt_secret,
                issuer=settings.api_jwt_issuer,
                audience=settings.api_jwt_audience,
                ttl_seconds=settings.api_refresh_token_ttl_seconds,
                access_ttl_seconds=settings.api_jwt_ttl_seconds,
                store=request.app.state.refresh_token_store,
            )
        except ValueError as exc:
            await audit_auth_event(
                request,
                principal=None,
                action="auth.refresh.rotate",
                outcome="failure",
                details={"status_code": 401},
            )
            raise HTTPException(status_code=401, detail="Invalid refresh token") from exc
        principal = verify_access_jwt(
            access_token,
            secret=settings.api_jwt_secret,
            issuer=settings.api_jwt_issuer,
            audience=settings.api_jwt_audience,
        )
        await audit_auth_event(
            request,
            principal=principal,
            action="auth.refresh.rotate",
            outcome="success",
        )
        return {
            "access_token": access_token,
            "refresh_token": next_refresh_token,
            "token_type": "Bearer",
            "expires_in": settings.api_jwt_ttl_seconds,
            "refresh_expires_in": settings.api_refresh_token_ttl_seconds,
        }


def register_local_auth_routes(app: FastAPI, settings: Config) -> None:  # noqa: C901
    """Attach the local-account register/verify/login routes to *app*."""

    def service(request: Request) -> LocalAuthService:
        if not settings.auth_local_enabled:
            raise HTTPException(status_code=404, detail="local authentication is not enabled")
        local_auth = getattr(request.app.state, "local_auth_service", None)
        if local_auth is None:
            raise HTTPException(status_code=503, detail="local authentication is not configured")
        return cast(LocalAuthService, local_auth)

    def mailer(request: Request) -> Mailer | None:
        return cast(Mailer | None, getattr(request.app.state, "verification_mailer", None))

    def deliver_verification(request: Request, email_normalized: str, token: str) -> bool:
        """Email the verification link; returns False when sending failed."""
        from doc_ray.adapters.mailer_smtp import verification_email
        from doc_ray.ports.mailer import MailerError

        outbound = mailer(request)
        if outbound is None:
            return False
        base = os.environ.get("DOCRAY_PUBLIC_BASE_URL", "").strip().rstrip("/")
        if not base:
            return False
        subject, body = verification_email(f"{base}/verify-email?token={token}")
        try:
            outbound.send(to=email_normalized, subject=subject, body=body)
        except MailerError:
            _logger.exception("verification email delivery failed for %s", email_normalized)
            return False
        return True

    def delivery_configured(request: Request) -> bool:
        return mailer(request) is not None and bool(
            os.environ.get("DOCRAY_PUBLIC_BASE_URL", "").strip()
        )

    def deliver_welcome(request: Request, email_normalized: str) -> bool:
        """Best-effort welcome email after verification; never fails the request.

        The account is already verified by the time this runs, so a welcome-mail
        failure is logged and swallowed rather than surfaced to the caller.
        """
        from doc_ray.adapters.mailer_smtp import welcome_email
        from doc_ray.ports.mailer import MailerError

        outbound = mailer(request)
        if outbound is None:
            return False
        base = os.environ.get("DOCRAY_PUBLIC_BASE_URL", "").strip().rstrip("/")
        subject, body = welcome_email(app_url=base or None)
        try:
            outbound.send(to=email_normalized, subject=subject, body=body)
        except MailerError:
            _logger.exception("welcome email delivery failed for %s", email_normalized)
            return False
        return True

    @app.post("/auth/register", status_code=202)
    async def register_local_account(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        """Register a self-service local account pending email verification."""
        if not settings.auth_registration_enabled:
            raise HTTPException(status_code=404, detail="local registration is not enabled")
        local_auth = service(request)
        if not delivery_configured(request) and not settings.auth_expose_verification_token:
            # Fail closed BEFORE creating the account: the raw token is
            # discarded after this request (only its hash is stored), so
            # without a delivery path the address would be stranded forever.
            raise HTTPException(
                status_code=503,
                detail=(
                    "registration is unavailable: no verification email delivery "
                    "is configured (set DOCRAY_SMTP_HOST and DOCRAY_PUBLIC_BASE_URL)"
                ),
            )
        try:
            display_name = (
                str(payload["display_name"]) if payload.get("display_name") is not None else None
            )
            result = local_auth.register(
                email=str(payload.get("email", "")),
                password=str(payload.get("password", "")),
                display_name=display_name,
            )
        except EmailAlreadyRegistered as exc:
            raise HTTPException(status_code=409, detail="email is already registered") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sent = deliver_verification(request, result.email_normalized, result.verification_token)
        response: dict[str, Any] = {
            "principal_id": result.principal_id,
            "email": result.email_normalized,
            "email_verification_required": True,
            "verification_email_sent": sent,
        }
        if settings.auth_expose_verification_token:
            response["verification_token"] = result.verification_token
        return response

    @app.post("/auth/resend-verification", status_code=202)
    async def resend_local_verification(
        payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        """Reissue a verification link; the response never reveals account state."""
        token = service(request).resend_verification(email=str(payload.get("email", "")))
        if token is not None:
            deliver_verification(request, str(payload.get("email", "")).strip().lower(), token)
        return {"accepted": True}

    @app.post("/auth/verify-email")
    async def verify_local_email(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        """Verify a local account email address."""
        try:
            result = service(request).verify_email(str(payload.get("token", "")))
        except VerificationTokenExpired as exc:
            raise HTTPException(status_code=410, detail="email verification token expired") from exc
        except VerificationTokenInvalid as exc:
            raise HTTPException(
                status_code=400,
                detail="email verification token is invalid",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        welcomed = deliver_welcome(request, result.email_normalized)
        return {
            "principal_id": result.principal_id,
            "email": result.email_normalized,
            "verified": True,
            "welcome_email_sent": welcomed,
        }

    @app.post("/auth/login")
    async def login_local_account(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        """Authenticate a verified local account and return RLS-bound API tokens."""
        try:
            local_principal = service(request).login(
                email=str(payload.get("email", "")),
                password=str(payload.get("password", "")),
            )
        except EmailNotVerified as exc:
            raise HTTPException(status_code=403, detail="email verification required") from exc
        except DisabledPrincipal as exc:
            raise HTTPException(status_code=403, detail="principal is disabled") from exc
        except InvalidCredentials as exc:
            raise HTTPException(status_code=401, detail="invalid email or password") from exc
        except LocalAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        principal = Principal(
            subject=local_principal.subject,
            principal_id=local_principal.principal_id,
            scopes=local_principal.scopes,
        )
        return _issue_jwt_pair(principal=principal, request=request, settings=settings)
