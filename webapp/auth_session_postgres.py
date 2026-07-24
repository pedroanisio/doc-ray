"""Durable Postgres webapp auth-session store (the ``web_auth_sessions`` table).

Same interface and semantics as ``InMemoryAuthSessionStore`` — create returns an
opaque id, get revokes-then-nulls an expired session, revoke/reset delete — but
persisted, so a webapp restart or a second replica no longer wipes every login
(migration 112 created the table for exactly this store).

Connections are opened per operation with the raw ``DATABASE_URL`` (the ``docray``
owner), mirroring the API's ``PostgresRefreshTokenStore``: the table is a webapp
control-plane artifact, not corpus data, so it carries no RLS and is reached
outside the per-request ``docray_client`` role.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row

if TYPE_CHECKING:  # avoid importing at module load; auth_session imports this lazily.
    from webapp.auth_session import WebAuthSession

_INSERT = (
    "INSERT INTO web_auth_sessions "
    "(session_id, principal_id, subject, bearer_token, issued_at, expires_at) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)
_SELECT = (
    "SELECT session_id, principal_id, subject, bearer_token, issued_at, expires_at "
    "FROM web_auth_sessions WHERE session_id = %s"
)


class PostgresAuthSessionStore:
    """Server-side webapp auth sessions persisted in ``web_auth_sessions``."""

    def __init__(self, database_url: str) -> None:
        """Store the connection URL; connections are opened per operation."""
        self._database_url = database_url

    def create(
        self,
        *,
        principal_id: str,
        subject: str,
        ttl_seconds: int | None = None,
        bearer_token: str | None = None,
    ) -> str:
        """Persist a new session and return its opaque id."""
        from webapp.auth_session import _utcnow, web_session_ttl_seconds

        now = _utcnow()
        sid = secrets.token_urlsafe(32)
        ttl = ttl_seconds if ttl_seconds is not None else web_session_ttl_seconds()
        with self._connect() as conn:
            conn.execute(
                _INSERT,
                (sid, principal_id, subject, bearer_token, now, now + timedelta(seconds=ttl)),
            )
        return sid

    def get(self, session_id: str | None) -> WebAuthSession | None:
        """Return the active session, deleting it first if it has expired."""
        from webapp.auth_session import WebAuthSession, _utcnow

        if not session_id:
            return None
        with self._connect() as conn:
            row = conn.execute(_SELECT, (session_id,)).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= _utcnow():
            self.revoke(session_id)
            return None
        return WebAuthSession(
            session_id=row["session_id"],
            principal_id=str(row["principal_id"]),
            subject=row["subject"],
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
            bearer_token=row["bearer_token"],
        )

    def revoke(self, session_id: str | None) -> None:
        """Delete a session id if present."""
        if not session_id:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM web_auth_sessions WHERE session_id = %s", (session_id,))

    def reset(self) -> None:
        """Delete every session; used by local diagnostics (never in a request)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM web_auth_sessions")

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self._database_url, autocommit=True, row_factory=dict_row)
