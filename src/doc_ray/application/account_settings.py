"""Account self-service: view identity, rename, change password / email.

This is the write-side counterpart to :mod:`doc_ray.application.local_auth`,
which only registers, verifies, and authenticates. Here a *signed-in* principal
edits its own data:

* ``update_display_name`` — rename (available to every principal kind).
* ``change_password`` — re-verify the current password, then rotate the hash
  (local accounts only).
* ``request_email_change`` / ``confirm_email_change`` — a two-step, ownership-
  proving flow: a token is mailed to the *new* address and the change only
  lands when that token is confirmed, so a typo can never strand the account.

Persistence is abstracted behind :class:`AccountRepository` so the domain rules
stay unit-testable without a database. All hashing uses the same argon2id
parameters as local registration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from doc_ray.application.local_auth import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    LocalAuthError,
    VerificationTokenExpired,
    VerificationTokenInvalid,
    hash_token,
    normalize_email,
)

#: Minimum local-account password length (kept in lockstep with registration).
MIN_PASSWORD_LENGTH = 12


class EmailUnchanged(LocalAuthError):
    """Raised when an email change targets the address already on file."""


class DisplayNameInvalid(LocalAuthError):
    """Raised when a display name is empty or only whitespace."""


class ExternalIdentityImmutable(LocalAuthError):
    """Raised when a credential-only action is attempted on an SSO identity."""


@dataclass(frozen=True)
class AccountView:
    """Read model of a principal's account, for the settings screen."""

    principal_id: str
    subject: str
    display_name: str
    kind: str
    is_local: bool
    email: str | None = None
    email_verified: bool = False
    external_subject: str | None = None
    pending_email: str | None = None
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmailChangeRequest:
    """A pending email change awaiting confirmation at the new address."""

    principal_id: str
    new_email: str
    token: str


@dataclass(frozen=True)
class EmailChangeResult:
    """The outcome of confirming an email change."""

    principal_id: str
    new_email: str


class AccountRepository(Protocol):
    """Persistence surface required by :class:`AccountService`."""

    def account_overview(self, principal_id: str) -> dict[str, Any] | None:
        """Return the principal, its local credential (if any), and roles."""
        ...

    def update_display_name(self, *, principal_id: str, display_name: str, now: datetime) -> None:
        """Persist a new display name for the principal."""
        ...

    def update_password_hash(self, *, principal_id: str, password_hash: str, now: datetime) -> None:
        """Persist a rotated local password hash."""
        ...

    def email_in_use(
        self, *, tenant_id: str, email_normalized: str, exclude_principal_id: str
    ) -> bool:
        """Return whether another principal in the tenant already owns the email."""
        ...

    def store_email_change_request(
        self,
        *,
        principal_id: str,
        token_hash: str,
        new_email_normalized: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        """Store a hashed email-change token bound to the new address."""
        ...

    def discard_email_change_requests(self, *, principal_id: str, now: datetime) -> None:
        """Invalidate every outstanding email-change request for a principal."""
        ...

    def consume_email_change_request(
        self, *, token_hash: str, now: datetime
    ) -> dict[str, Any] | None:
        """Consume an email-change token, returning its principal and new email."""
        ...

    def apply_email_change(
        self, *, principal_id: str, new_email_normalized: str, now: datetime
    ) -> None:
        """Move the credential to the confirmed new email, keeping it verified."""
        ...


class AccountService:
    """Self-service account edits for an authenticated principal."""

    def __init__(
        self,
        repository: AccountRepository,
        *,
        now: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
        password_hasher: PasswordHasher | None = None,
        email_change_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        """Initialize the account service."""
        self._repo = repository
        self._now = now or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or _default_token_factory
        self._hasher = password_hasher or PasswordHasher()
        self._email_change_ttl = email_change_ttl

    def account(self, principal_id: str) -> AccountView:
        """Return the account read model for the settings screen."""
        row = self._require_account(principal_id)
        roles = tuple(sorted(str(r) for r in (row.get("roles") or []) if r))
        password_hash = row.get("password_hash")
        return AccountView(
            principal_id=str(row["principal_id"]),
            subject=str(row.get("external_subject") or f"principal:{principal_id}"),
            display_name=str(row.get("display_name") or ""),
            kind=str(row.get("kind") or "human"),
            is_local=password_hash is not None,
            email=(str(row["email_normalized"]) if row.get("email_normalized") else None),
            email_verified=row.get("email_verified_at") is not None,
            external_subject=(
                str(row["external_subject"]) if row.get("external_subject") else None
            ),
            pending_email=(
                str(row["pending_email_normalized"])
                if row.get("pending_email_normalized")
                else None
            ),
            roles=roles,
        )

    def update_display_name(self, *, principal_id: str, display_name: str) -> str:
        """Rename the principal; returns the stored (trimmed) display name."""
        self._require_account(principal_id)
        clean = (display_name or "").strip()
        if not clean:
            raise DisplayNameInvalid("display name cannot be empty")
        self._repo.update_display_name(
            principal_id=principal_id, display_name=clean, now=self._now()
        )
        return clean

    def change_password(
        self, *, principal_id: str, current_password: str, new_password: str
    ) -> None:
        """Rotate a local password after re-verifying the current one."""
        row = self._require_local_account(principal_id)
        try:
            self._hasher.verify(str(row["password_hash"]), current_password)
        except VerifyMismatchError as exc:
            raise InvalidCredentials("current password is incorrect") from exc
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
        if new_password == current_password:
            raise ValueError("new password must differ from the current password")
        self._repo.update_password_hash(
            principal_id=principal_id,
            password_hash=self._hasher.hash(new_password),
            now=self._now(),
        )

    def request_email_change(self, *, principal_id: str, new_email: str) -> EmailChangeRequest:
        """Begin an email change: validate, then mint a token for the new address.

        The credential is NOT moved yet — the returned token must be delivered to
        ``new_email`` and confirmed via :meth:`confirm_email_change`. Any prior
        pending request for this principal is invalidated first.
        """
        row = self._require_local_account(principal_id)
        new_normalized = normalize_email(new_email)
        if new_normalized == str(row.get("email_normalized") or ""):
            raise EmailUnchanged("that is already your email address")
        if self._repo.email_in_use(
            tenant_id=str(row["tenant_id"]),
            email_normalized=new_normalized,
            exclude_principal_id=principal_id,
        ):
            raise EmailAlreadyRegistered(new_normalized)
        now = self._now()
        self._repo.discard_email_change_requests(principal_id=principal_id, now=now)
        token = self._token_factory()
        self._repo.store_email_change_request(
            principal_id=principal_id,
            token_hash=hash_token(token),
            new_email_normalized=new_normalized,
            expires_at=now + self._email_change_ttl,
            now=now,
        )
        return EmailChangeRequest(principal_id=principal_id, new_email=new_normalized, token=token)

    def confirm_email_change(self, token: str) -> EmailChangeResult:
        """Confirm ownership of the new address and move the credential to it."""
        now = self._now()
        row = self._repo.consume_email_change_request(token_hash=hash_token(token), now=now)
        if row is None:
            raise VerificationTokenInvalid("email change token is invalid")
        principal_id = str(row["principal_id"])
        new_normalized = str(row["new_email_normalized"])
        # Re-check uniqueness at confirmation time: another account may have
        # claimed the address during the token's lifetime.
        if self._repo.email_in_use(
            tenant_id=str(row["tenant_id"]),
            email_normalized=new_normalized,
            exclude_principal_id=principal_id,
        ):
            raise EmailAlreadyRegistered(new_normalized)
        self._repo.apply_email_change(
            principal_id=principal_id, new_email_normalized=new_normalized, now=now
        )
        return EmailChangeResult(principal_id=principal_id, new_email=new_normalized)

    def _require_account(self, principal_id: str) -> dict[str, Any]:
        row = self._repo.account_overview(principal_id)
        if row is None:
            raise LocalAuthError("account not found")
        return row

    def _require_local_account(self, principal_id: str) -> dict[str, Any]:
        row = self._require_account(principal_id)
        if row.get("password_hash") is None:
            raise ExternalIdentityImmutable(
                "this action is only available for local email/password accounts"
            )
        return row


def _default_token_factory() -> str:
    import secrets

    return secrets.token_urlsafe(32)


__all__ = [
    "AccountService",
    "AccountRepository",
    "AccountView",
    "EmailChangeRequest",
    "EmailChangeResult",
    "EmailUnchanged",
    "DisplayNameInvalid",
    "ExternalIdentityImmutable",
    "EmailAlreadyRegistered",
    "InvalidCredentials",
    "VerificationTokenExpired",
    "VerificationTokenInvalid",
    "MIN_PASSWORD_LENGTH",
]
