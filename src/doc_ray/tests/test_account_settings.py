"""Unit tests for the self-service account edit domain service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from argon2 import PasswordHasher

from doc_ray.application.account_settings import (
    AccountService,
    DisplayNameInvalid,
    EmailAlreadyRegistered,
    EmailUnchanged,
    ExternalIdentityImmutable,
    InvalidCredentials,
    VerificationTokenExpired,
    VerificationTokenInvalid,
)
from doc_ray.application.local_auth import hash_token

_CLOCK = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
# Cheap argon2 parameters keep the hashing-heavy tests fast without changing
# behaviour (the same $argon2id$ verification path is exercised).
_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


class _FakeAccountRepository:
    def __init__(self) -> None:
        self.accounts: dict[str, dict[str, Any]] = {}
        self.change_requests: dict[str, dict[str, Any]] = {}

    def account_overview(self, principal_id: str) -> dict[str, Any] | None:
        row = self.accounts.get(principal_id)
        return dict(row) if row is not None else None

    def update_display_name(self, *, principal_id: str, display_name: str, now: datetime) -> None:
        self.accounts[principal_id]["display_name"] = display_name

    def update_password_hash(self, *, principal_id: str, password_hash: str, now: datetime) -> None:
        self.accounts[principal_id]["password_hash"] = password_hash

    def email_in_use(
        self, *, tenant_id: str, email_normalized: str, exclude_principal_id: str
    ) -> bool:
        return any(
            pid != exclude_principal_id
            and row.get("tenant_id") == tenant_id
            and row.get("email_normalized") == email_normalized
            for pid, row in self.accounts.items()
        )

    def store_email_change_request(
        self,
        *,
        principal_id: str,
        token_hash: str,
        new_email_normalized: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        self.change_requests[token_hash] = {
            "principal_id": principal_id,
            "new_email_normalized": new_email_normalized,
            "tenant_id": self.accounts[principal_id]["tenant_id"],
            "expires_at": expires_at,
            "consumed_at": None,
        }

    def discard_email_change_requests(self, *, principal_id: str, now: datetime) -> None:
        for row in self.change_requests.values():
            if row["principal_id"] == principal_id and row["consumed_at"] is None:
                row["consumed_at"] = now

    def consume_email_change_request(
        self, *, token_hash: str, now: datetime
    ) -> dict[str, Any] | None:
        row = self.change_requests.get(token_hash)
        if row is None or row["consumed_at"] is not None:
            return None
        if row["expires_at"] <= now:
            raise VerificationTokenExpired("email change token expired")
        row["consumed_at"] = now
        return {
            "principal_id": row["principal_id"],
            "new_email_normalized": row["new_email_normalized"],
            "tenant_id": row["tenant_id"],
        }

    def apply_email_change(
        self, *, principal_id: str, new_email_normalized: str, now: datetime
    ) -> None:
        self.accounts[principal_id]["email_normalized"] = new_email_normalized
        self.accounts[principal_id]["email_verified_at"] = now


def _service(repo: _FakeAccountRepository, *, tokens: list[str] | None = None) -> AccountService:
    supply = iter(tokens or ["change-token"])
    return AccountService(
        repo,
        now=lambda: _CLOCK,
        token_factory=lambda: next(supply),
        password_hasher=_HASHER,
    )


def _seed_local(repo: _FakeAccountRepository, **overrides: Any) -> str:
    pid = overrides.pop("principal_id", "p-local")
    row = {
        "principal_id": pid,
        "tenant_id": "t-1",
        "display_name": "Ada Lovelace",
        "kind": "human",
        "external_subject": "local:ada@example.com",
        "email_normalized": "ada@example.com",
        "password_hash": _HASHER.hash("correct horse battery staple"),
        "email_verified_at": _CLOCK,
        "pending_email_normalized": None,
        "roles": ["reader"],
    }
    row.update(overrides)
    repo.accounts[pid] = row
    return pid


def _seed_external(repo: _FakeAccountRepository, pid: str = "p-sso") -> str:
    repo.accounts[pid] = {
        "principal_id": pid,
        "tenant_id": "t-1",
        "display_name": "SSO User",
        "kind": "human",
        "external_subject": "oidc:sso@example.com",
        "email_normalized": None,
        "password_hash": None,
        "email_verified_at": None,
        "pending_email_normalized": None,
        "roles": ["reader"],
    }
    return pid


def test_account_view_reports_local_identity() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    view = _service(repo).account(pid)
    assert view.is_local is True
    assert view.email == "ada@example.com"
    assert view.email_verified is True
    assert view.roles == ("reader",)


def test_account_view_reports_external_identity() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_external(repo)
    view = _service(repo).account(pid)
    assert view.is_local is False
    assert view.email is None
    assert view.external_subject == "oidc:sso@example.com"


def test_update_display_name_trims_and_persists() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    result = _service(repo).update_display_name(principal_id=pid, display_name="  Ada B.  ")
    assert result == "Ada B."
    assert repo.accounts[pid]["display_name"] == "Ada B."


def test_update_display_name_rejects_blank() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    with pytest.raises(DisplayNameInvalid):
        _service(repo).update_display_name(principal_id=pid, display_name="   ")


def test_change_password_requires_correct_current() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    with pytest.raises(InvalidCredentials):
        _service(repo).change_password(
            principal_id=pid, current_password="wrong", new_password="a-brand-new-password"
        )


def test_change_password_rejects_short_new() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    with pytest.raises(ValueError, match="12 characters"):
        _service(repo).change_password(
            principal_id=pid,
            current_password="correct horse battery staple",
            new_password="short",
        )


def test_change_password_rejects_unchanged() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    with pytest.raises(ValueError, match="differ"):
        _service(repo).change_password(
            principal_id=pid,
            current_password="correct horse battery staple",
            new_password="correct horse battery staple",
        )


def test_change_password_rotates_hash_on_success() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    old_hash = repo.accounts[pid]["password_hash"]
    _service(repo).change_password(
        principal_id=pid,
        current_password="correct horse battery staple",
        new_password="a fresh and long passphrase",
    )
    new_hash = repo.accounts[pid]["password_hash"]
    assert new_hash != old_hash
    assert _HASHER.verify(new_hash, "a fresh and long passphrase")


def test_change_password_blocked_for_external_identity() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_external(repo)
    with pytest.raises(ExternalIdentityImmutable):
        _service(repo).change_password(
            principal_id=pid, current_password="x", new_password="a fresh and long passphrase"
        )


def test_request_email_change_rejects_same_address() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    with pytest.raises(EmailUnchanged):
        _service(repo).request_email_change(principal_id=pid, new_email="ADA@example.com")


def test_request_email_change_rejects_taken_address() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    _seed_local(repo, principal_id="p-other", email_normalized="taken@example.com")
    with pytest.raises(EmailAlreadyRegistered):
        _service(repo).request_email_change(principal_id=pid, new_email="taken@example.com")


def test_request_email_change_mints_token_and_discards_prior() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    svc = _service(repo, tokens=["tok-1", "tok-2"])
    first = svc.request_email_change(principal_id=pid, new_email="first@example.com")
    second = svc.request_email_change(principal_id=pid, new_email="second@example.com")
    assert first.token == "tok-1" and second.token == "tok-2"
    assert second.new_email == "second@example.com"
    # The first request is invalidated, so only the second confirms.
    assert repo.change_requests[hash_token("tok-1")]["consumed_at"] is not None
    # Credential unchanged until confirmation.
    assert repo.accounts[pid]["email_normalized"] == "ada@example.com"


def test_request_email_change_blocked_for_external_identity() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_external(repo)
    with pytest.raises(ExternalIdentityImmutable):
        _service(repo).request_email_change(principal_id=pid, new_email="new@example.com")


def test_confirm_email_change_moves_credential() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    svc = _service(repo, tokens=["tok-confirm"])
    svc.request_email_change(principal_id=pid, new_email="new@example.com")
    result = svc.confirm_email_change("tok-confirm")
    assert result.new_email == "new@example.com"
    assert repo.accounts[pid]["email_normalized"] == "new@example.com"
    assert repo.accounts[pid]["email_verified_at"] == _CLOCK


def test_confirm_email_change_rejects_unknown_token() -> None:
    repo = _FakeAccountRepository()
    _seed_local(repo)
    with pytest.raises(VerificationTokenInvalid):
        _service(repo).confirm_email_change("nope")


def test_confirm_email_change_rejects_expired_token() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    svc = AccountService(
        repo,
        now=lambda: _CLOCK,
        token_factory=lambda: "tok-exp",
        password_hasher=_HASHER,
        email_change_ttl=timedelta(hours=1),
    )
    svc.request_email_change(principal_id=pid, new_email="new@example.com")
    expired = AccountService(
        repo,
        now=lambda: _CLOCK + timedelta(hours=2),
        password_hasher=_HASHER,
    )
    with pytest.raises(VerificationTokenExpired):
        expired.confirm_email_change("tok-exp")


def test_confirm_email_change_rechecks_uniqueness_at_confirmation() -> None:
    repo = _FakeAccountRepository()
    pid = _seed_local(repo)
    svc = _service(repo, tokens=["tok-race"])
    svc.request_email_change(principal_id=pid, new_email="race@example.com")
    # Someone else claims the address before confirmation.
    _seed_local(repo, principal_id="p-rival", email_normalized="race@example.com")
    with pytest.raises(EmailAlreadyRegistered):
        svc.confirm_email_change("tok-race")
    assert repo.accounts[pid]["email_normalized"] == "ada@example.com"
