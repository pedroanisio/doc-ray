"""Durable Postgres refresh-token store (the ``auth_refresh_tokens`` table).

Same rotation semantics as the API's in-memory store — one active use per
token, reuse revokes the whole family, expiry is recorded — but persisted, so
API restarts and horizontal scale-out no longer revoke every session
(migration 074 created the table for exactly this store).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row

if TYPE_CHECKING:  # runtime import is deferred: doc_ray.entrypoints.api's
    # __init__ eagerly builds the ASGI app, which imports this adapter — a
    # module-level import here would be circular.
    from doc_ray.entrypoints.api.security import RefreshTokenRecord

_logger = logging.getLogger("doc_ray.auth")


class PostgresRefreshTokenStore:
    """Refresh-token rotation state persisted in ``auth_refresh_tokens``."""

    def __init__(self, database_url: str) -> None:
        """Store the connection URL; connections are opened per operation."""
        self._database_url = database_url

    def remember(self, record: RefreshTokenRecord) -> None:
        """Record a newly-issued active refresh token."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_refresh_tokens
                    (token_id, family_id, principal_id, subject, scopes,
                     expires_at, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'active')
                """,
                (
                    record.token_id,
                    record.family_id,
                    record.principal.principal_id,
                    record.principal.subject or "",
                    sorted(record.principal.scopes),
                    record.expires_at,
                ),
            )

    def consume(self, token_id: str, *, now: datetime) -> RefreshTokenRecord:
        """Consume one active refresh token, marking it rotated.

        Reuse of a rotated/revoked token revokes the whole family before
        raising — the BL-072 detection behavior, now durable across restarts.
        """
        # The status side effects (expired / family revocation) must PERSIST
        # even though consume() then raises — so each terminal branch commits
        # its own transaction and the raise happens after it, never inside it
        # (a raise inside `with conn.transaction()` would roll the update back).
        with self._connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT token_id, family_id, principal_id, subject, scopes, "
                    "       expires_at, status "
                    "  FROM auth_refresh_tokens WHERE token_id = %s FOR UPDATE",
                    (token_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("refresh token is unknown")
                outcome = self._classify(row, now)
                if outcome == "expired":
                    conn.execute(
                        "UPDATE auth_refresh_tokens SET status = 'expired', updated_at = %s "
                        "WHERE token_id = %s",
                        (now, token_id),
                    )
                elif outcome == "reused":
                    self._revoke_family(conn, str(row["family_id"]), now)
                    _logger.warning(
                        "refresh token reuse detected; revoked family %s for principal %s",
                        row["family_id"],
                        row["principal_id"],
                    )
                else:
                    conn.execute(
                        "UPDATE auth_refresh_tokens SET status = 'rotated', updated_at = %s "
                        "WHERE token_id = %s",
                        (now, token_id),
                    )
            # Transaction has committed here; safe to raise without losing it.
            if outcome == "expired":
                raise ValueError("refresh token expired")
            if outcome == "reused":
                raise ValueError("refresh token reused")
            from doc_ray.entrypoints.api.security import Principal, RefreshTokenRecord

            return RefreshTokenRecord(
                token_id=str(row["token_id"]),
                family_id=str(row["family_id"]),
                principal=Principal(
                    subject=str(row["subject"]) or None,
                    principal_id=str(row["principal_id"]),
                    scopes=frozenset(row["scopes"] or []),
                ),
                expires_at=row["expires_at"],
                status="rotated",
            )

    @staticmethod
    def _classify(row: dict[str, Any], now: datetime) -> str:
        if row["expires_at"] <= now:
            return "expired"
        if row["status"] != "active":
            return "reused"
        return "active"

    def revoke_family(self, family_id: str) -> None:
        """Mark every token in *family_id* revoked."""
        with self._connect() as conn:
            self._revoke_family(conn, family_id, None)

    def _revoke_family(
        self, conn: psycopg.Connection[Any], family_id: str, now: datetime | None
    ) -> None:
        conn.execute(
            "UPDATE auth_refresh_tokens "
            "   SET status = 'revoked', updated_at = COALESCE(%s, now()) "
            " WHERE family_id = %s",
            (now, family_id),
        )

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self._database_url, autocommit=True, row_factory=dict_row)
