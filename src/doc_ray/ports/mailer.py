"""Outbound mail port for account-verification delivery."""

from __future__ import annotations

from typing import Protocol


class MailerError(RuntimeError):
    """Raised when a mail delivery attempt fails."""


class Mailer(Protocol):
    """Minimal outbound-mail surface required by the auth flows."""

    def send(self, *, to: str, subject: str, body: str) -> None:
        """Deliver one plain-text message; raise :class:`MailerError` on failure."""
        ...
