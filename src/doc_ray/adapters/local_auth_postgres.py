"""Postgres persistence for local self-service authentication."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from doc_ray.application.local_auth import EmailAlreadyRegistered, VerificationTokenExpired


class PostgresLocalAuthRepository:
    """Repository for local credentials and email verification tokens."""

    def __init__(self, database_url: str) -> None:
        """Initialize the repository with a database URL."""
        self._database_url = database_url

    def local_identity_by_email(self, email_normalized: str) -> dict[str, Any] | None:
        """Return local credential state for an email."""
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT pc.principal_id, p.tenant_id, pc.email_normalized,
                       pc.password_hash, pc.email_verified_at, pc.disabled_at,
                       pc.failed_login_count, pc.last_failed_login_at,
                       p.disabled_at AS principal_disabled_at, p.active
                  FROM principal_credentials pc
                  JOIN principals p ON p.id = pc.principal_id
                 WHERE pc.email_normalized = %s
                 LIMIT 1
                """,
                (email_normalized,),
            ).fetchone()

    def permission_actions(self, principal_id: str) -> frozenset[str]:
        """Return the permission actions carried by the principal's roles.

        Drives the token's coarse ``write`` scope (see
        :func:`~doc_ray.application.local_auth.scopes_for_actions`). Read
        directly from the RBAC join rather than inferred from a role name, so
        re-granting a role changes what its members may do without a code
        change.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT p.action
                  FROM principal_roles pr
                  JOIN role_permissions rp ON rp.role_id = pr.role_id
                  JOIN permissions p       ON p.id = rp.permission_id
                 WHERE pr.principal_id = %s
                """,
                (principal_id,),
            ).fetchall()
        return frozenset(str(row["action"]) for row in rows)

    def create_local_identity(
        self,
        *,
        email_normalized: str,
        display_name: str,
        password_hash: str,
        tenant_id: str | None,
        default_role: str,
        now: datetime,
    ) -> dict[str, Any]:
        """Create a principal, default role binding, and local credential."""
        try:
            with self._connect() as conn, conn.transaction():
                principal = conn.execute(
                    """
                    INSERT INTO principals (kind, display_name, external_subject, tenant_id)
                    VALUES ('human', %s, %s, COALESCE(%s::uuid, docray_default_tenant_id()))
                    RETURNING id, tenant_id, display_name
                    """,
                    (display_name, f"local:{email_normalized}", tenant_id),
                ).fetchone()
                if principal is None:  # pragma: no cover - RETURNING makes this unreachable.
                    raise RuntimeError("failed to create principal")
                role = conn.execute(
                    "SELECT id FROM roles WHERE name = %s",
                    (default_role,),
                ).fetchone()
                if role is None:
                    raise ValueError(f"unknown default role: {default_role}")
                conn.execute(
                    "INSERT INTO principal_roles (principal_id, role_id) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (principal["id"], role["id"]),
                )
                credential = conn.execute(
                    """
                    INSERT INTO principal_credentials
                        (principal_id, tenant_id, email_normalized, password_hash,
                         password_algo, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'argon2id', %s, %s)
                    RETURNING principal_id, tenant_id, email_normalized, password_hash,
                              email_verified_at, disabled_at
                    """,
                    (
                        principal["id"],
                        principal["tenant_id"],
                        email_normalized,
                        password_hash,
                        now,
                        now,
                    ),
                ).fetchone()
        except UniqueViolation as exc:
            raise EmailAlreadyRegistered(email_normalized) from exc
        if credential is None:  # pragma: no cover - RETURNING makes this unreachable.
            raise RuntimeError("failed to create local credential")
        return credential

    def store_email_verification_token(
        self,
        *,
        principal_id: str,
        token_hash: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        """Store a hashed email verification token."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO email_verification_tokens
                    (token_hash, principal_id, expires_at, created_at)
                VALUES (%s, %s, %s, %s)
                """,
                (token_hash, principal_id, expires_at, now),
            )

    def consume_email_verification_token(
        self,
        *,
        token_hash: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        """Consume a verification token and return the verified credential row."""
        with self._connect() as conn, conn.transaction():
            token = conn.execute(
                """
                SELECT token_hash, principal_id, expires_at, consumed_at
                  FROM email_verification_tokens
                 WHERE token_hash = %s
                 FOR UPDATE
                """,
                (token_hash,),
            ).fetchone()
            if token is None or token["consumed_at"] is not None:
                return None
            if token["expires_at"] <= now:
                raise VerificationTokenExpired("email verification token expired")
            conn.execute(
                "UPDATE email_verification_tokens SET consumed_at = %s WHERE token_hash = %s",
                (now, token_hash),
            )
            return conn.execute(
                """
                UPDATE principal_credentials
                   SET email_verified_at = COALESCE(email_verified_at, %s),
                       updated_at = %s
                 WHERE principal_id = %s
                 RETURNING principal_id, tenant_id, email_normalized, password_hash,
                           email_verified_at, disabled_at
                """,
                (now, now, token["principal_id"]),
            ).fetchone()

    def mark_login_success(self, *, principal_id: str, now: datetime) -> None:
        """Record a successful local login."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE principal_credentials
                   SET failed_login_count = 0, last_login_at = %s, updated_at = %s
                 WHERE principal_id = %s
                """,
                (now, now, principal_id),
            )

    def discard_email_verification_tokens(self, *, principal_id: str, now: datetime) -> None:
        """Invalidate every outstanding verification token for a principal."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE email_verification_tokens
                   SET consumed_at = %s
                 WHERE principal_id = %s AND consumed_at IS NULL
                """,
                (now, principal_id),
            )

    def mark_login_failure(self, *, email_normalized: str, now: datetime) -> None:
        """Record a failed local login attempt."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE principal_credentials
                   SET failed_login_count = failed_login_count + 1,
                       last_failed_login_at = %s,
                       updated_at = %s
                 WHERE email_normalized = %s
                """,
                (now, now, email_normalized),
            )

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._database_url, autocommit=True, row_factory=dict_row)
