"""Local self-service authentication domain service."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError


class LocalAuthError(ValueError):
    """Base error for local authentication failures."""


class EmailAlreadyRegistered(LocalAuthError):
    """Raised when registration attempts to reuse an email in a tenant."""


class InvalidCredentials(LocalAuthError):
    """Raised when login credentials are invalid."""


class EmailNotVerified(LocalAuthError):
    """Raised when a local account has not verified its email."""


class DisabledPrincipal(LocalAuthError):
    """Raised when a principal or local credential is disabled."""


class VerificationTokenInvalid(LocalAuthError):
    """Raised when an email verification token is unknown or consumed."""


class VerificationTokenExpired(LocalAuthError):
    """Raised when an email verification token is expired."""


@dataclass(frozen=True)
class RegistrationResult:
    """Result of local self-service registration."""

    principal_id: str
    email_normalized: str
    verification_token: str


@dataclass(frozen=True)
class VerificationResult:
    """Result of consuming a valid email verification token."""

    principal_id: str
    email_normalized: str
    scopes: frozenset[str] = frozenset({"read"})


@dataclass(frozen=True)
class LocalPrincipal:
    """Principal-shaped identity returned by local authentication."""

    subject: str
    principal_id: str
    scopes: frozenset[str]


#: Permission actions that imply the principal may mutate something. A token's
#: coarse ``write`` scope is derived from these rather than from a role-name
#: allowlist, so a new role carrying ``annotate`` works without touching this
#: module — and ``reader`` stays genuinely read-only, which SCIM and the admin
#: CLI both rely on when provisioning look-but-don't-touch accounts.
WRITE_ACTIONS = frozenset({"annotate", "delete", "redact", "admin"})


def scopes_for_actions(actions: frozenset[str]) -> frozenset[str]:
    """Map RBAC permission actions onto the coarse API token scopes.

    Every authenticated principal reads; only one holding a mutating permission
    gets ``write``. Row-level authorization stays with RLS — this scope is the
    coarse gate ``_require_write_audited`` gates mutations on, not the boundary
    that decides *which* documents may be touched.
    """
    scopes = {"read"}
    if actions & WRITE_ACTIONS:
        scopes.add("write")
    return frozenset(scopes)


class LocalAuthRepository(Protocol):
    """Persistence surface required by :class:`LocalAuthService`."""

    def local_identity_by_email(self, email_normalized: str) -> dict[str, Any] | None:
        """Return local credential state for an email."""
        ...

    def permission_actions(self, principal_id: str) -> frozenset[str]:
        """Return the permission actions the principal's roles carry."""
        ...

    def create_local_identity(
        self,
        *,
        email_normalized: str,
        display_name: str,
        password_hash: str,
        tenant_id: str | None,
        default_role: str,
        now: datetime,
    ) -> dict[str, Any]:
        """Create a principal, default role binding, and local credential."""
        ...

    def store_email_verification_token(
        self,
        *,
        principal_id: str,
        token_hash: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        """Store a hashed email verification token."""
        ...

    def consume_email_verification_token(
        self,
        *,
        token_hash: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        """Consume a verification token and return the verified credential row."""
        ...

    def mark_login_success(self, *, principal_id: str, now: datetime) -> None:
        """Record a successful local login."""
        ...

    def mark_login_failure(self, *, email_normalized: str, now: datetime) -> None:
        """Record a failed local login attempt."""
        ...

    def discard_email_verification_tokens(self, *, principal_id: str, now: datetime) -> None:
        """Invalidate every outstanding verification token for a principal."""
        ...


class LocalAuthService:
    """Register, verify, and authenticate local first-party users."""

    def __init__(
        self,
        repository: LocalAuthRepository,
        *,
        now: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
        password_hasher: PasswordHasher | None = None,
        verification_ttl: timedelta = timedelta(hours=24),
        default_role: str = "member",
        max_failed_logins: int = 10,
        lockout_seconds: int = 900,
    ) -> None:
        """Initialize the local auth service."""
        self._repo = repository
        self._now = now or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._hasher = password_hasher or PasswordHasher()
        self._verification_ttl = verification_ttl
        self._default_role = default_role
        self._max_failed_logins = max(1, max_failed_logins)
        self._lockout = timedelta(seconds=max(0, lockout_seconds))

    def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None = None,
        tenant_id: str | None = None,
    ) -> RegistrationResult:
        """Create a reader principal and pending local credential."""
        email_normalized = normalize_email(email)
        if self._repo.local_identity_by_email(email_normalized) is not None:
            raise EmailAlreadyRegistered(email_normalized)
        clean_display_name = (display_name or email_normalized).strip() or email_normalized
        password_hash = self._hasher.hash(_validate_password(password))
        now = self._now()
        row = self._repo.create_local_identity(
            email_normalized=email_normalized,
            display_name=clean_display_name,
            password_hash=password_hash,
            tenant_id=tenant_id,
            default_role=self._default_role,
            now=now,
        )
        token = self._token_factory()
        self._repo.store_email_verification_token(
            principal_id=str(row["principal_id"]),
            token_hash=hash_token(token),
            expires_at=now + self._verification_ttl,
            now=now,
        )
        return RegistrationResult(
            principal_id=str(row["principal_id"]),
            email_normalized=email_normalized,
            verification_token=token,
        )

    def resend_verification(self, *, email: str) -> str | None:
        """Issue a fresh verification token for a pending registration.

        Returns the raw token to deliver, or ``None`` when the email is
        unknown, already verified, or disabled — callers must respond
        identically in every case so this cannot be used to enumerate
        accounts. The previous token is discarded, so a lost or undelivered
        verification email can never permanently strand an address.
        """
        try:
            email_normalized = normalize_email(email)
        except ValueError:
            return None
        row = self._repo.local_identity_by_email(email_normalized)
        if row is None or row.get("email_verified_at") is not None:
            return None
        if row.get("disabled_at") is not None or row.get("principal_disabled_at") is not None:
            return None
        now = self._now()
        principal_id = str(row["principal_id"])
        self._repo.discard_email_verification_tokens(principal_id=principal_id, now=now)
        token = self._token_factory()
        self._repo.store_email_verification_token(
            principal_id=principal_id,
            token_hash=hash_token(token),
            expires_at=now + self._verification_ttl,
            now=now,
        )
        return token

    def verify_email(self, token: str) -> VerificationResult:
        """Consume an email verification token and enable local login."""
        row = self._repo.consume_email_verification_token(
            token_hash=hash_token(token),
            now=self._now(),
        )
        if row is None:
            raise VerificationTokenInvalid("email verification token is invalid")
        principal_id = str(row["principal_id"])
        return VerificationResult(
            principal_id=principal_id,
            email_normalized=str(row["email_normalized"]),
            # The post-verification auto-login forwards these; deriving them the
            # same way login does stops verification from silently handing back
            # a read-only token to a member who may write.
            scopes=scopes_for_actions(self._repo.permission_actions(principal_id)),
        )

    def login(self, *, email: str, password: str) -> LocalPrincipal:
        """Authenticate a verified local user and return an RLS principal."""
        email_normalized = normalize_email(email)
        row = self._repo.local_identity_by_email(email_normalized)
        if row is None:
            self._repo.mark_login_failure(email_normalized=email_normalized, now=self._now())
            raise InvalidCredentials("invalid email or password")
        if (
            row.get("disabled_at") is not None
            or row.get("principal_disabled_at") is not None
            or row.get("active") is False
        ):
            raise DisabledPrincipal("principal is disabled")
        if self._locked_out(row):
            # Same message as a bad password: the lockout must not become an
            # account-state oracle. No failure is recorded here, so attackers
            # cannot extend a victim's lockout indefinitely.
            raise InvalidCredentials("invalid email or password")
        try:
            verified = self._hasher.verify(str(row["password_hash"]), password)
        except VerifyMismatchError as exc:
            self._repo.mark_login_failure(email_normalized=email_normalized, now=self._now())
            raise InvalidCredentials("invalid email or password") from exc
        if not verified:
            self._repo.mark_login_failure(email_normalized=email_normalized, now=self._now())
            raise InvalidCredentials("invalid email or password")
        if row.get("email_verified_at") is None:
            raise EmailNotVerified("email is not verified")
        principal_id = str(row["principal_id"])
        self._repo.mark_login_success(principal_id=principal_id, now=self._now())
        return LocalPrincipal(
            subject=f"local:{email_normalized}",
            principal_id=principal_id,
            scopes=scopes_for_actions(self._repo.permission_actions(principal_id)),
        )

    def scopes_for_principal(self, principal_id: str) -> frozenset[str]:
        """Return the coarse API scopes a principal is entitled to, from its RBAC.

        The same derivation ``login`` uses (:func:`scopes_for_actions` over the
        principal's own permission actions), exposed so the OAuth consent path can
        bound an issued token to the user's real capability rather than blanket-
        granting ``write``. Works for any principal id (local, OIDC, SAML): one
        with no RBAC actions resolves to read-only.
        """
        return scopes_for_actions(self._repo.permission_actions(principal_id))

    def _locked_out(self, row: dict[str, Any]) -> bool:
        """Return whether the credential is inside its brute-force lockout window."""
        failures = int(row.get("failed_login_count") or 0)
        last_failed = row.get("last_failed_login_at")
        if failures < self._max_failed_logins or not isinstance(last_failed, datetime):
            return False
        return self._now() < last_failed + self._lockout


def normalize_email(email: str) -> str:
    """Return the canonical email key for local auth."""
    normalized = email.strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise ValueError("valid email is required")
    return normalized


def hash_token(token: str) -> str:
    """Hash a bearer-style verification token before storage."""
    clean = token.strip()
    if not clean:
        raise ValueError("token is required")
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def _validate_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    return password
