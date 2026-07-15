"""Postgres persistence for self-service account edits.

Uses a privileged application connection (no ``SET ROLE``), matching
:class:`doc_ray.adapters.local_auth_postgres.PostgresLocalAuthRepository`:
these writes target ``principals`` / ``principal_credentials`` /
``email_change_requests``, which sit outside RLS and are trusted to the
application. UUID parameters are cast explicitly (``%s::uuid``) so string
principal ids compare cleanly against uuid columns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from doc_ray.application.account_settings import EmailAlreadyRegistered
from doc_ray.application.local_auth import VerificationTokenExpired


class PostgresAccountRepository:
    """Repository for account display name, password, and email changes."""

    def __init__(self, database_url: str) -> None:
        """Initialize the repository with a database URL."""
        self._database_url = database_url

    def account_overview(self, principal_id: str) -> dict[str, Any] | None:
        """Return the principal joined to its local credential and roles."""
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT p.id AS principal_id, p.tenant_id, p.display_name, p.kind,
                       p.external_subject,
                       pc.email_normalized, pc.password_hash, pc.email_verified_at,
                       (SELECT ecr.new_email_normalized
                          FROM email_change_requests ecr
                         WHERE ecr.principal_id = p.id
                           AND ecr.consumed_at IS NULL
                           AND ecr.expires_at > now()
                         ORDER BY ecr.created_at DESC
                         LIMIT 1) AS pending_email_normalized,
                       (SELECT COALESCE(array_agg(r.name ORDER BY r.name), '{}')
                          FROM principal_roles prr
                          JOIN roles r ON r.id = prr.role_id
                         WHERE prr.principal_id = p.id) AS roles
                  FROM principals p
                  LEFT JOIN principal_credentials pc ON pc.principal_id = p.id
                 WHERE p.id = %s::uuid
                """,
                (principal_id,),
            ).fetchone()

    def update_display_name(self, *, principal_id: str, display_name: str, now: datetime) -> None:
        """Persist a new display name for the principal."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE principals SET display_name = %s WHERE id = %s::uuid",
                (display_name, principal_id),
            )

    def update_password_hash(self, *, principal_id: str, password_hash: str, now: datetime) -> None:
        """Persist a rotated local password hash."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE principal_credentials
                   SET password_hash = %s, password_algo = 'argon2id', updated_at = %s
                 WHERE principal_id = %s::uuid
                """,
                (password_hash, now, principal_id),
            )

    def email_in_use(
        self, *, tenant_id: str, email_normalized: str, exclude_principal_id: str
    ) -> bool:
        """Return whether another principal in the tenant owns the email."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                  FROM principal_credentials
                 WHERE tenant_id = %s::uuid
                   AND email_normalized = %s
                   AND principal_id <> %s::uuid
                 LIMIT 1
                """,
                (tenant_id, email_normalized, exclude_principal_id),
            ).fetchone()
            return row is not None

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
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO email_change_requests
                    (token_hash, principal_id, new_email_normalized, expires_at, created_at)
                VALUES (%s, %s::uuid, %s, %s, %s)
                """,
                (token_hash, principal_id, new_email_normalized, expires_at, now),
            )

    def discard_email_change_requests(self, *, principal_id: str, now: datetime) -> None:
        """Invalidate every outstanding email-change request for a principal."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE email_change_requests
                   SET consumed_at = %s
                 WHERE principal_id = %s::uuid AND consumed_at IS NULL
                """,
                (now, principal_id),
            )

    def consume_email_change_request(
        self, *, token_hash: str, now: datetime
    ) -> dict[str, Any] | None:
        """Consume an email-change token, returning principal and new email."""
        with self._connect() as conn, conn.transaction():
            row = conn.execute(
                """
                SELECT ecr.principal_id, ecr.new_email_normalized, ecr.expires_at,
                       ecr.consumed_at, p.tenant_id
                  FROM email_change_requests ecr
                  JOIN principals p ON p.id = ecr.principal_id
                 WHERE ecr.token_hash = %s
                 FOR UPDATE OF ecr
                """,
                (token_hash,),
            ).fetchone()
            if row is None or row["consumed_at"] is not None:
                return None
            if row["expires_at"] <= now:
                raise VerificationTokenExpired("email change token expired")
            conn.execute(
                "UPDATE email_change_requests SET consumed_at = %s WHERE token_hash = %s",
                (now, token_hash),
            )
            return {
                "principal_id": str(row["principal_id"]),
                "new_email_normalized": str(row["new_email_normalized"]),
                "tenant_id": str(row["tenant_id"]),
            }

    def apply_email_change(
        self, *, principal_id: str, new_email_normalized: str, now: datetime
    ) -> None:
        """Move the credential to the confirmed new email, keeping it verified."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE principal_credentials
                       SET email_normalized = %s,
                           email_verified_at = COALESCE(email_verified_at, %s),
                           updated_at = %s
                     WHERE principal_id = %s::uuid
                    """,
                    (new_email_normalized, now, now, principal_id),
                )
        except UniqueViolation as exc:
            raise EmailAlreadyRegistered(new_email_normalized) from exc

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._database_url, autocommit=True, row_factory=dict_row)
