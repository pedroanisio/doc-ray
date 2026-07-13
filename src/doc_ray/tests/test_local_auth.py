"""Local self-service authentication service tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from doc_ray.application.local_auth import (
    EmailAlreadyRegistered,
    EmailNotVerified,
    InvalidCredentials,
    LocalAuthService,
    VerificationTokenExpired,
    VerificationTokenInvalid,
)

_ROOT = Path(__file__).resolve().parents[3]


class _FakeLocalAuthRepository:
    def __init__(self) -> None:
        self.credentials: dict[str, dict[str, object]] = {}
        self.tokens: dict[str, dict[str, object]] = {}
        self.roles: dict[str, list[str]] = {}
        self.created: list[dict[str, object]] = []

    def local_identity_by_email(self, email_normalized: str) -> dict[str, object] | None:
        return self.credentials.get(email_normalized)

    def create_local_identity(
        self,
        *,
        email_normalized: str,
        display_name: str,
        password_hash: str,
        tenant_id: str | None,
        default_role: str,
        now: datetime,
    ) -> dict[str, object]:
        if email_normalized in self.credentials:
            raise EmailAlreadyRegistered(email_normalized)
        principal_id = f"principal-{len(self.credentials) + 1}"
        row = {
            "principal_id": principal_id,
            "tenant_id": tenant_id or "tenant-default",
            "email_normalized": email_normalized,
            "display_name": display_name,
            "password_hash": password_hash,
            "email_verified_at": None,
            "disabled_at": None,
            "active": True,
        }
        self.credentials[email_normalized] = row
        self.roles[principal_id] = [default_role]
        self.created.append(row)
        return row

    def store_email_verification_token(
        self,
        *,
        principal_id: str,
        token_hash: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        self.tokens[token_hash] = {
            "principal_id": principal_id,
            "expires_at": expires_at,
            "consumed_at": None,
        }

    def consume_email_verification_token(
        self,
        *,
        token_hash: str,
        now: datetime,
    ) -> dict[str, object] | None:
        row = self.tokens.get(token_hash)
        if row is None or row["consumed_at"] is not None:
            return None
        if row["expires_at"] <= now:
            raise VerificationTokenExpired("email verification token expired")
        row["consumed_at"] = now
        for credential in self.credentials.values():
            if credential["principal_id"] == row["principal_id"]:
                credential["email_verified_at"] = now
                return credential
        return None

    def mark_login_success(self, *, principal_id: str, now: datetime) -> None:
        for row in self.credentials.values():
            if row["principal_id"] == principal_id:
                row["failed_login_count"] = 0
                row["last_login_at"] = now

    def mark_login_failure(self, *, email_normalized: str, now: datetime) -> None:
        row = self.credentials.get(email_normalized)
        if row is not None:
            row["failed_login_count"] = int(row.get("failed_login_count") or 0) + 1
            row["last_failed_login_at"] = now

    def discard_email_verification_tokens(self, *, principal_id: str, now: datetime) -> None:
        for row in self.tokens.values():
            if row["principal_id"] == principal_id and row["consumed_at"] is None:
                row["consumed_at"] = now


def test_register_user_creates_unverified_reader_principal_with_argon2id_hash() -> None:
    repo = _FakeLocalAuthRepository()
    service = LocalAuthService(
        repo,
        now=lambda: datetime(2026, 7, 12, tzinfo=UTC),
        token_factory=lambda: "verify-token",
    )

    result = service.register(
        email="ALICE@example.COM ",
        password="correct horse battery staple",
        display_name="Alice",
    )

    row = repo.credentials["alice@example.com"]
    assert result.principal_id == "principal-1"
    assert result.email_normalized == "alice@example.com"
    assert result.verification_token == "verify-token"
    assert row["email_verified_at"] is None
    assert str(row["password_hash"]).startswith("$argon2")
    assert "correct horse battery staple" not in str(row["password_hash"])
    assert repo.roles["principal-1"] == ["reader"]
    assert "admin" not in repo.roles["principal-1"]


def test_local_auth_migration_stores_hashes_and_requires_verified_email() -> None:
    migration = (_ROOT / "db" / "migrations" / "074_local_auth.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS principal_credentials" in migration
    assert "password_hash" in migration
    assert "CHECK (password_hash LIKE '$argon2%')" in migration
    assert "email_verified_at" in migration
    assert "timestamptz" in migration
    assert "UNIQUE (tenant_id, email_normalized)" in migration
    assert "CREATE TABLE IF NOT EXISTS email_verification_tokens" in migration
    assert "token_hash" in migration
    assert "PRIMARY KEY CHECK (length(token_hash) = 64)" in migration
    assert "verification_token text" not in migration
    assert "token text" not in migration
    assert "auth_refresh_tokens" in migration


def test_login_requires_verified_email_even_with_valid_password() -> None:
    repo = _FakeLocalAuthRepository()
    service = LocalAuthService(repo, token_factory=lambda: "verify-token")
    service.register(email="alice@example.com", password="correct horse battery staple")

    with pytest.raises(EmailNotVerified):
        service.login(email="alice@example.com", password="correct horse battery staple")


def test_verify_email_consumes_token_and_allows_login() -> None:
    repo = _FakeLocalAuthRepository()
    service = LocalAuthService(repo, token_factory=lambda: "verify-token")
    registration = service.register(
        email="alice@example.com",
        password="correct horse battery staple",
    )

    verified = service.verify_email(registration.verification_token)
    principal = service.login(
        email="alice@example.com",
        password="correct horse battery staple",
    )

    assert verified.principal_id == registration.principal_id
    assert principal.principal_id == registration.principal_id
    assert principal.scopes == frozenset({"read"})
    assert principal.subject == "local:alice@example.com"


def test_login_rejects_wrong_password_without_revealing_account_state() -> None:
    repo = _FakeLocalAuthRepository()
    service = LocalAuthService(repo, token_factory=lambda: "verify-token")
    registration = service.register(
        email="alice@example.com",
        password="correct horse battery staple",
    )
    service.verify_email(registration.verification_token)

    with pytest.raises(InvalidCredentials):
        service.login(email="alice@example.com", password="wrong password")


def test_login_locks_out_after_max_failures_even_with_correct_password() -> None:
    """Regression for the brute-force MAJOR: failed_login_count was write-only.
    After max_failed_logins failures, login is refused for lockout_seconds even
    with the correct password, then works again once the window passes — and a
    success resets the counter."""
    current = {"now": datetime(2026, 7, 13, tzinfo=UTC)}
    repo = _FakeLocalAuthRepository()
    service = LocalAuthService(
        repo,
        now=lambda: current["now"],
        token_factory=lambda: "verify-token",
        max_failed_logins=3,
        lockout_seconds=900,
    )
    registration = service.register(
        email="alice@example.com", password="correct horse battery staple"
    )
    service.verify_email(registration.verification_token)

    for _ in range(3):
        with pytest.raises(InvalidCredentials):
            service.login(email="alice@example.com", password="wrong password")

    with pytest.raises(InvalidCredentials) as locked:
        service.login(email="alice@example.com", password="correct horse battery staple")
    # Identical message to a plain bad password: lockout must not become an
    # account-state oracle.
    assert str(locked.value) == "invalid email or password"

    current["now"] += timedelta(seconds=901)
    principal = service.login(email="alice@example.com", password="correct horse battery staple")
    assert principal.principal_id == registration.principal_id
    assert repo.credentials["alice@example.com"]["failed_login_count"] == 0


def test_locked_out_login_does_not_extend_its_own_lockout() -> None:
    """Attempts during the locked window are rejected without re-marking
    failure — otherwise an attacker could keep a victim locked out forever."""
    current = {"now": datetime(2026, 7, 13, tzinfo=UTC)}
    repo = _FakeLocalAuthRepository()
    service = LocalAuthService(
        repo,
        now=lambda: current["now"],
        token_factory=lambda: "verify-token",
        max_failed_logins=2,
        lockout_seconds=900,
    )
    registration = service.register(
        email="alice@example.com", password="correct horse battery staple"
    )
    service.verify_email(registration.verification_token)
    for _ in range(2):
        with pytest.raises(InvalidCredentials):
            service.login(email="alice@example.com", password="wrong password")

    current["now"] += timedelta(seconds=899)
    with pytest.raises(InvalidCredentials):
        service.login(email="alice@example.com", password="wrong password")
    assert repo.credentials["alice@example.com"]["failed_login_count"] == 2

    current["now"] += timedelta(seconds=2)  # 901s after the LAST counted failure
    assert (
        service.login(
            email="alice@example.com", password="correct horse battery staple"
        ).principal_id
        == registration.principal_id
    )


def test_resend_verification_reissues_token_and_discards_the_old_one() -> None:
    """Regression for the delivery blocker: a lost/undelivered verification
    email must be recoverable — resend issues a fresh token and the stale one
    stops working, so no address is ever permanently stranded."""
    repo = _FakeLocalAuthRepository()
    tokens = iter(["first-token", "second-token"])
    service = LocalAuthService(repo, token_factory=lambda: next(tokens))
    service.register(email="alice@example.com", password="correct horse battery staple")

    fresh = service.resend_verification(email="alice@example.com")

    assert fresh == "second-token"
    with pytest.raises(VerificationTokenInvalid):
        service.verify_email("first-token")
    assert service.verify_email("second-token").email_normalized == "alice@example.com"


def test_resend_verification_returns_none_for_unknown_or_already_verified() -> None:
    repo = _FakeLocalAuthRepository()
    tokens = iter(["first-token", "second-token"])
    service = LocalAuthService(repo, token_factory=lambda: next(tokens))
    registration = service.register(
        email="alice@example.com", password="correct horse battery staple"
    )
    service.verify_email(registration.verification_token)

    assert service.resend_verification(email="alice@example.com") is None
    assert service.resend_verification(email="nobody@example.com") is None


def test_expired_verification_token_does_not_verify_email() -> None:
    now = datetime(2026, 7, 12, tzinfo=UTC)
    repo = _FakeLocalAuthRepository()
    service = LocalAuthService(
        repo,
        now=lambda: now,
        token_factory=lambda: "verify-token",
        verification_ttl=timedelta(seconds=1),
    )
    registration = service.register(
        email="alice@example.com",
        password="correct horse battery staple",
    )
    service._now = lambda: now + timedelta(seconds=2)

    with pytest.raises(VerificationTokenExpired):
        service.verify_email(registration.verification_token)
