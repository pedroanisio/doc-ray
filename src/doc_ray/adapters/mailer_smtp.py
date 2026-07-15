"""SMTP adapter for the outbound :class:`~doc_ray.ports.mailer.Mailer` port.

Uses only the standard library (``smtplib``/``email``). Configured entirely
from environment variables so the webapp and API share one setup:

    DOCRAY_SMTP_HOST        required to enable sending (unset = no mailer)
    DOCRAY_SMTP_PORT        default 587
    DOCRAY_SMTP_FROM        default noreply@<DOCRAY_SMTP_HOST>
    DOCRAY_SMTP_USERNAME    optional (with PASSWORD, enables AUTH)
    DOCRAY_SMTP_PASSWORD    optional
    DOCRAY_SMTP_STARTTLS    default true (ignored when implicit TLS is used)
    DOCRAY_SMTP_SSL         implicit TLS (SMTPS); default true on port 465, else false
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


def welcome_email(
    display_name: str | None = None, *, app_url: str | None = None
) -> tuple[str, str]:
    """Return the (subject, body) pair for the post-verification welcome message.

    Args:
        display_name: Optional recipient name for the greeting; a generic greeting
            is used when omitted.
        app_url: Optional application URL to point the new user at.

    Returns:
        The ``(subject, body)`` of the welcome message.
    """
    greeting = f"Hello {display_name}," if display_name and display_name.strip() else "Hello,"
    subject = "Welcome to doc-ray"
    lines = [
        greeting,
        "",
        "Your email address is verified and your doc-ray account is now active.",
        "You can sign in and start ingesting, decomposing, and exploring documents.",
    ]
    if app_url:
        lines += ["", f"    {app_url}"]
    lines += ["", "— The doc-ray team"]
    return subject, "\n".join(lines) + "\n"


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
        use_ssl: bool = False,
        timeout: float = 10.0,
    ) -> None:
        """Store the connection settings; no connection is made until send()."""
        self._host = host
        self._port = port
        self._sender = sender
        self._username = username
        self._password = password
        self._starttls = starttls
        self._use_ssl = use_ssl
        self._timeout = timeout

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SmtpMailer | None:
        """Build a mailer from DOCRAY_SMTP_* variables, or None when unset."""
        source = os.environ if env is None else env
        host = (source.get("DOCRAY_SMTP_HOST") or "").strip()
        if not host:
            return None
        port = int(source.get("DOCRAY_SMTP_PORT", "587"))
        # Implicit TLS (SMTPS) is the norm on port 465; an explicit DOCRAY_SMTP_SSL
        # overrides that inference in either direction.
        ssl_raw = (source.get("DOCRAY_SMTP_SSL") or "").strip().lower()
        use_ssl = ssl_raw in {"1", "true", "yes", "on"} if ssl_raw else port == 465
        return cls(
            host=host,
            port=port,
            sender=(source.get("DOCRAY_SMTP_FROM") or f"noreply@{host}").strip(),
            username=(source.get("DOCRAY_SMTP_USERNAME") or "").strip() or None,
            password=source.get("DOCRAY_SMTP_PASSWORD") or None,
            starttls=(source.get("DOCRAY_SMTP_STARTTLS", "true").strip().lower())
            in {"1", "true", "yes", "on"},
            use_ssl=use_ssl,
            timeout=float(source.get("DOCRAY_SMTP_TIMEOUT_SECONDS", "10")),
        )

    def send(self, *, to: str, subject: str, body: str) -> None:
        """Deliver one plain-text message; raise :class:`MailerError` on failure."""
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        # Implicit TLS opens an already-encrypted socket (SMTP_SSL); STARTTLS
        # upgrades a plaintext one. They are mutually exclusive — never STARTTLS
        # over an SMTPS connection.
        smtp_factory = smtplib.SMTP_SSL if self._use_ssl else smtplib.SMTP
        try:
            with smtp_factory(self._host, self._port, timeout=self._timeout) as smtp:
                if self._starttls and not self._use_ssl:
                    smtp.starttls()
                if self._username and self._password:
                    smtp.login(self._username, self._password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailerError(f"verification email delivery failed: {exc}") from exc
