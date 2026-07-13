"""SMTP adapter for the outbound :class:`~doc_ray.ports.mailer.Mailer` port.

Uses only the standard library (``smtplib``/``email``). Configured entirely
from environment variables so the webapp and API share one setup:

    DOCRAY_SMTP_HOST        required to enable sending (unset = no mailer)
    DOCRAY_SMTP_PORT        default 587
    DOCRAY_SMTP_FROM        default noreply@<DOCRAY_SMTP_HOST>
    DOCRAY_SMTP_USERNAME    optional (with PASSWORD, enables AUTH)
    DOCRAY_SMTP_PASSWORD    optional
    DOCRAY_SMTP_STARTTLS    default true
    DOCRAY_SMTP_TIMEOUT_SECONDS  default 10
"""

from __future__ import annotations

import os
import smtplib
from collections.abc import Mapping
from email.message import EmailMessage

from doc_ray.ports.mailer import MailerError


def verification_email(verify_url: str, *, ttl_hours: int = 24) -> tuple[str, str]:
    """Return the (subject, body) pair for an email-verification message."""
    subject = "Verify your doc-ray email address"
    body = (
        "Hello,\n\n"
        "An account was registered with this email address. To activate it,\n"
        "open the verification link below:\n\n"
        f"    {verify_url}\n\n"
        f"The link expires in {ttl_hours} hours. If you did not register,\n"
        "you can ignore this message — the account cannot be used without\n"
        "verification.\n"
    )
    return subject, body


class SmtpMailer:
    """Deliver mail over SMTP with optional STARTTLS and authentication."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 587,
        sender: str,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = True,
        timeout: float = 10.0,
    ) -> None:
        """Store the connection settings; no connection is made until send()."""
        self._host = host
        self._port = port
        self._sender = sender
        self._username = username
        self._password = password
        self._starttls = starttls
        self._timeout = timeout

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SmtpMailer | None:
        """Build a mailer from DOCRAY_SMTP_* variables, or None when unset."""
        source = os.environ if env is None else env
        host = (source.get("DOCRAY_SMTP_HOST") or "").strip()
        if not host:
            return None
        return cls(
            host=host,
            port=int(source.get("DOCRAY_SMTP_PORT", "587")),
            sender=(source.get("DOCRAY_SMTP_FROM") or f"noreply@{host}").strip(),
            username=(source.get("DOCRAY_SMTP_USERNAME") or "").strip() or None,
            password=source.get("DOCRAY_SMTP_PASSWORD") or None,
            starttls=(source.get("DOCRAY_SMTP_STARTTLS", "true").strip().lower())
            in {"1", "true", "yes", "on"},
            timeout=float(source.get("DOCRAY_SMTP_TIMEOUT_SECONDS", "10")),
        )

    def send(self, *, to: str, subject: str, body: str) -> None:
        """Deliver one plain-text message; raise :class:`MailerError` on failure."""
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                if self._starttls:
                    smtp.starttls()
                if self._username and self._password:
                    smtp.login(self._username, self._password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailerError(f"verification email delivery failed: {exc}") from exc
