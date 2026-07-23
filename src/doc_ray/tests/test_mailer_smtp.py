"""SMTP verification-mailer adapter tests (smtplib is faked; no network)."""

from __future__ import annotations

import smtplib

import pytest

from doc_ray.adapters import mailer_smtp
from doc_ray.ports.mailer import MailerError


class _RecordingSMTP:
    """Stands in for smtplib.SMTP: records the protocol conversation."""

    instances: list[_RecordingSMTP] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[tuple[str, tuple]] = []
        self.message = None
        _RecordingSMTP.instances.append(self)

    def __enter__(self) -> _RecordingSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append(("quit", ()))

    def starttls(self) -> None:
        self.calls.append(("starttls", ()))

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", (username, password)))

    def send_message(self, message) -> None:
        self.calls.append(("send_message", ()))
        self.message = message

    def noop(self) -> tuple[int, bytes]:
        self.calls.append(("noop", ()))
        return 250, b"OK"


class _RecordingSMTPSSL(_RecordingSMTP):
    """Stands in for smtplib.SMTP_SSL (implicit TLS); shares the instance log."""


def test_from_env_returns_none_when_no_host_is_configured() -> None:
    assert mailer_smtp.SmtpMailer.from_env(env={}) is None


def test_from_env_builds_mailer_that_sends_over_starttls(monkeypatch) -> None:
    _RecordingSMTP.instances.clear()
    monkeypatch.setattr(mailer_smtp.smtplib, "SMTP", _RecordingSMTP)
    mailer = mailer_smtp.SmtpMailer.from_env(
        env={
            "DOCRAY_SMTP_HOST": "mail.example.com",
            "DOCRAY_SMTP_PORT": "2587",
            "DOCRAY_SMTP_USERNAME": "mailer",
            "DOCRAY_SMTP_PASSWORD": "s3cret",
            "DOCRAY_SMTP_FROM": "doc-ray <noreply@example.com>",
        }
    )
    assert mailer is not None

    mailer.send(to="alice@example.com", subject="Verify", body="https://x/verify?token=t")

    smtp = _RecordingSMTP.instances[-1]
    assert (smtp.host, smtp.port) == ("mail.example.com", 2587)
    assert ("starttls", ()) in smtp.calls
    assert ("login", ("mailer", "s3cret")) in smtp.calls
    assert smtp.message["To"] == "alice@example.com"
    assert smtp.message["From"] == "doc-ray <noreply@example.com>"
    assert smtp.message["Subject"] == "Verify"
    assert "token=t" in smtp.message.get_content()


def test_from_env_uses_implicit_tls_on_port_465(monkeypatch) -> None:
    """Port 465 is SMTPS (implicit TLS): connect via SMTP_SSL, never STARTTLS."""
    _RecordingSMTP.instances.clear()
    monkeypatch.setattr(mailer_smtp.smtplib, "SMTP", _RecordingSMTP)
    monkeypatch.setattr(mailer_smtp.smtplib, "SMTP_SSL", _RecordingSMTPSSL)
    mailer = mailer_smtp.SmtpMailer.from_env(
        env={
            "DOCRAY_SMTP_HOST": "mail.example.com",
            "DOCRAY_SMTP_PORT": "465",
            "DOCRAY_SMTP_USERNAME": "mailer",
            "DOCRAY_SMTP_PASSWORD": "s3cret",
        }
    )
    assert mailer is not None

    mailer.send(to="alice@example.com", subject="Verify", body="https://x/verify?token=t")

    smtp = _RecordingSMTP.instances[-1]
    assert isinstance(smtp, _RecordingSMTPSSL)
    assert (smtp.host, smtp.port) == ("mail.example.com", 465)
    assert ("starttls", ()) not in smtp.calls
    assert ("login", ("mailer", "s3cret")) in smtp.calls
    assert ("send_message", ()) in smtp.calls


def test_explicit_ssl_flag_overrides_the_port_465_default(monkeypatch) -> None:
    """DOCRAY_SMTP_SSL is authoritative when set: off on 465 falls back to
    plaintext+STARTTLS; on for 587 forces implicit TLS."""
    _RecordingSMTP.instances.clear()
    monkeypatch.setattr(mailer_smtp.smtplib, "SMTP", _RecordingSMTP)
    monkeypatch.setattr(mailer_smtp.smtplib, "SMTP_SSL", _RecordingSMTPSSL)

    off = mailer_smtp.SmtpMailer.from_env(
        env={"DOCRAY_SMTP_HOST": "h", "DOCRAY_SMTP_PORT": "465", "DOCRAY_SMTP_SSL": "false"}
    )
    assert off is not None
    off.send(to="a@example.com", subject="s", body="b")
    assert not isinstance(_RecordingSMTP.instances[-1], _RecordingSMTPSSL)

    on = mailer_smtp.SmtpMailer.from_env(
        env={"DOCRAY_SMTP_HOST": "h", "DOCRAY_SMTP_PORT": "587", "DOCRAY_SMTP_SSL": "true"}
    )
    assert on is not None
    on.send(to="a@example.com", subject="s", body="b")
    assert isinstance(_RecordingSMTP.instances[-1], _RecordingSMTPSSL)


def test_send_wraps_smtp_failures_in_mailer_error(monkeypatch) -> None:
    class _ExplodingSMTP(_RecordingSMTP):
        def send_message(self, message) -> None:
            raise smtplib.SMTPException("relay refused")

    monkeypatch.setattr(mailer_smtp.smtplib, "SMTP", _ExplodingSMTP)
    mailer = mailer_smtp.SmtpMailer(host="mail.example.com", port=587, sender="noreply@example.com")

    with pytest.raises(MailerError):
        mailer.send(to="alice@example.com", subject="Verify", body="body")


def test_verify_opens_an_authenticated_session_and_delivers_nothing(monkeypatch) -> None:
    """verify() exercises the same TLS+AUTH path as send() without sending mail."""
    _RecordingSMTP.instances.clear()
    monkeypatch.setattr(mailer_smtp.smtplib, "SMTP", _RecordingSMTP)
    mailer = mailer_smtp.SmtpMailer(
        host="mail.smtp2go.com",
        port=587,
        sender="noreply@example.com",
        username="mailer",
        password="s3cret",
    )

    mailer.verify()

    smtp = _RecordingSMTP.instances[-1]
    assert ("starttls", ()) in smtp.calls
    assert ("login", ("mailer", "s3cret")) in smtp.calls
    assert ("noop", ()) in smtp.calls
    assert ("send_message", ()) not in smtp.calls


def test_verify_wraps_authentication_failures_in_mailer_error(monkeypatch) -> None:
    class _RejectingSMTP(_RecordingSMTP):
        def login(self, username: str, password: str) -> None:
            raise smtplib.SMTPAuthenticationError(535, b"Authentication failed")

    monkeypatch.setattr(mailer_smtp.smtplib, "SMTP", _RejectingSMTP)
    mailer = mailer_smtp.SmtpMailer(
        host="mail.smtp2go.com", port=587, sender="noreply@example.com", username="u", password="p"
    )

    with pytest.raises(MailerError, match="535"):
        mailer.verify()


def test_describe_reports_the_transport_without_leaking_the_password() -> None:
    mailer = mailer_smtp.SmtpMailer(
        host="mail.smtp2go.com",
        port=587,
        sender="noreply@doc-ray.com",
        username="doc-ray.com",
        password="s3cret",
    )

    described = mailer.describe()

    assert "mail.smtp2go.com:587" in described
    assert "starttls" in described
    assert "doc-ray.com" in described
    assert "s3cret" not in described


def test_verification_email_body_contains_the_link_and_expiry() -> None:
    subject, body = mailer_smtp.verification_email(
        "https://docs.example.com/verify-email?token=abc", ttl_hours=24
    )
    assert "verify" in subject.lower()
    assert "https://docs.example.com/verify-email?token=abc" in body
    assert "24" in body


def test_welcome_email_greets_and_links_when_given_name_and_url() -> None:
    subject, body = mailer_smtp.welcome_email("Ada", app_url="https://doc-ray.com")
    assert "welcome" in subject.lower()
    assert "Hello Ada," in body
    assert "https://doc-ray.com" in body
    assert "active" in body.lower()


def test_welcome_email_uses_generic_greeting_without_a_name() -> None:
    _, body = mailer_smtp.welcome_email()
    assert body.startswith("Hello,")
