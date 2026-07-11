"""Postgres adapter for auth administration use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


@dataclass(frozen=True)
class PrincipalRecord:
    """A row from ``principals`` needed by auth administration commands."""

    id: str
    display_name: str
    kind: str
    external_subject: str | None


class PostgresAuthAdminRepository:
    """Repository over doc-ray's RBAC tables."""

    def __init__(self, conn: psycopg.Connection[dict[str, Any]]) -> None:
        """Initialize the repository with an open autocommit connection."""
        self._conn = conn

    @classmethod
    def from_database_url(cls, database_url: str) -> PostgresAuthAdminRepository:
        """Open a repository connection from a database URL."""
        conn = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
        return cls(conn)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def resolve_principal(self, value: str) -> PrincipalRecord:
        """Resolve a principal by UUID or display name."""
        column = "display_name"
        try:
            UUID(value)
        except ValueError:
            params = (value,)
        else:
            column = "id"
            params = (value,)
        row = self._conn.execute(
            f"SELECT id, display_name, kind, external_subject FROM principals WHERE {column} = %s",
            params,
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown principal: {value}")
        return _principal_from_row(row)

    def create_principal(
        self, *, display_name: str, kind: str, external_subject: str | None = None
    ) -> PrincipalRecord:
        """Create a principal, or return the existing row with the same display name."""
        row = self._conn.execute(
            "INSERT INTO principals (kind, display_name, external_subject) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (display_name) DO UPDATE SET display_name = EXCLUDED.display_name "
            "RETURNING id, display_name, kind, external_subject",
            (kind, display_name, external_subject),
        ).fetchone()
        if row is None:  # pragma: no cover - RETURNING makes this unreachable
            raise RuntimeError(f"failed to create principal: {display_name}")
        return _principal_from_row(row)

    def assign_role(self, principal_id: str, role_name: str) -> None:
        """Assign a named RBAC role to a principal."""
        role = self._conn.execute("SELECT id FROM roles WHERE name = %s", (role_name,)).fetchone()
        if role is None:
            raise ValueError(f"unknown role: {role_name}")
        self._conn.execute(
            "INSERT INTO principal_roles (principal_id, role_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (principal_id, role["id"]),
        )

    def grant_document(self, principal_id: str, document_id: str, permission: str) -> None:
        """Grant one document ACL permission to a principal."""
        self._conn.execute(
            "INSERT INTO document_acl (document_id, principal_id, permission) VALUES (%s, %s, %s) "
            "ON CONFLICT (document_id, principal_id, permission) DO NOTHING",
            (document_id, principal_id, permission),
        )

    def create_db_login(
        self, principal_id: str, role_name: str, password: str | None = None
    ) -> str:
        """Create or bind a direct-SQL LOGIN role for a principal."""
        row = self._conn.execute(
            "SELECT docray_create_principal_login(%s, %s, %s) AS role_name",
            (principal_id, role_name, password),
        ).fetchone()
        if row is None:  # pragma: no cover - function always returns
            raise RuntimeError(f"failed to create database login role: {role_name}")
        return str(row["role_name"])


def _principal_from_row(row: dict[str, Any]) -> PrincipalRecord:
    """Map a ``principals`` row to a public record."""
    return PrincipalRecord(
        id=str(row["id"]),
        display_name=str(row["display_name"]),
        kind=str(row["kind"]),
        external_subject=(
            str(row["external_subject"]) if row.get("external_subject") is not None else None
        ),
    )
