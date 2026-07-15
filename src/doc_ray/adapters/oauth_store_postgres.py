"""Postgres persistence for the OAuth 2.1 Authorization Server.

Privileged application connection (no ``SET ROLE``), matching the other auth
tables (see 082_oauth_clients.sql): OAuth clients and authorization codes are
plumbing outside RLS. Authorization codes are consumed atomically (``FOR UPDATE``
+ ``consumed_at``) so a replayed code can never be redeemed twice.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from doc_ray.application.oauth_server import RegisteredClient, sha256_hex


class PostgresOAuthClientStore:
    """Store for dynamically registered OAuth clients."""

    def __init__(self, database_url: str) -> None:
        """Initialize the client store."""
        self._database_url = database_url

    def create_client(self, client: RegisteredClient, *, secret_hash: str | None) -> None:
        """Persist a newly registered client."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO oauth_clients
                    (client_id, client_secret_hash, redirect_uris, client_name,
                     token_endpoint_auth_method, grant_types, scope)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    client.client_id,
                    secret_hash,
                    list(client.redirect_uris),
                    client.client_name,
                    client.token_endpoint_auth_method,
                    list(client.grant_types),
                    client.scope,
                ),
            )

    def get_client(self, client_id: str) -> RegisteredClient | None:
        """Return a registered client by id (never the secret hash)."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT client_id, redirect_uris, client_name,
                       token_endpoint_auth_method, grant_types, scope
                  FROM oauth_clients
                 WHERE client_id = %s
                """,
                (client_id,),
            ).fetchone()
        if row is None:
            return None
        return RegisteredClient(
            client_id=str(row["client_id"]),
            redirect_uris=tuple(row["redirect_uris"]),
            client_name=row["client_name"],
            token_endpoint_auth_method=str(row["token_endpoint_auth_method"]),
            grant_types=tuple(row["grant_types"]),
            scope=str(row["scope"]),
        )

    def verify_client_secret(self, client_id: str, secret: str) -> bool:
        """Return whether *secret* hashes to the stored client secret hash."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT client_secret_hash FROM oauth_clients WHERE client_id = %s",
                (client_id,),
            ).fetchone()
        stored = row["client_secret_hash"] if row else None
        if not stored:
            return False
        return secrets.compare_digest(sha256_hex(secret), str(stored))

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._database_url, autocommit=True, row_factory=dict_row)


class PostgresAuthorizationCodeStore:
    """Store for short-lived, single-use authorization codes."""

    def __init__(self, database_url: str) -> None:
        """Initialize the authorization-code store."""
        self._database_url = database_url

    def store_code(self, *, code_hash: str, record: dict[str, Any]) -> None:
        """Persist a hashed authorization code and its bound context."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO oauth_authorization_codes
                    (code_hash, client_id, redirect_uri, code_challenge, code_challenge_method,
                     principal_id, subject, scope, resource, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s::uuid, %s, %s, %s, %s)
                """,
                (
                    code_hash,
                    record["client_id"],
                    record["redirect_uri"],
                    record["code_challenge"],
                    record["code_challenge_method"],
                    record["principal_id"],
                    record["subject"],
                    record["scope"],
                    record.get("resource"),
                    record["expires_at"],
                ),
            )

    def consume_code(self, *, code_hash: str, now: datetime) -> dict[str, Any] | None:
        """Atomically consume a code (single-use); returns its record or ``None``.

        A code that is unknown, already consumed, or expired yields ``None``. An
        expired-but-unconsumed row is still marked consumed so it cannot be
        redeemed later even if its clock skews.
        """
        with self._connect() as conn, conn.transaction():
            row = conn.execute(
                """
                SELECT client_id, redirect_uri, code_challenge, code_challenge_method,
                       principal_id, subject, scope, resource, expires_at, consumed_at
                  FROM oauth_authorization_codes
                 WHERE code_hash = %s
                 FOR UPDATE
                """,
                (code_hash,),
            ).fetchone()
            if row is None or row["consumed_at"] is not None:
                return None
            conn.execute(
                "UPDATE oauth_authorization_codes SET consumed_at = %s WHERE code_hash = %s",
                (now, code_hash),
            )
            if row["expires_at"] <= now:
                return None
            return {
                "client_id": str(row["client_id"]),
                "redirect_uri": str(row["redirect_uri"]),
                "code_challenge": str(row["code_challenge"]),
                "code_challenge_method": str(row["code_challenge_method"]),
                "principal_id": str(row["principal_id"]),
                "subject": str(row["subject"]),
                "scope": str(row["scope"]),
                "resource": (str(row["resource"]) if row["resource"] else None),
                "expires_at": row["expires_at"],
            }

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._database_url, autocommit=True, row_factory=dict_row)
