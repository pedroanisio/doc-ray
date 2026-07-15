"""Postgres persistence for per-principal UI preferences.

Uses a privileged application connection (no ``SET ROLE``), matching the other
account tables (see 079_account_settings.sql): preferences are cosmetic, carry
no authorization weight, and live outside RLS.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row


class PostgresPreferencesRepository:
    """Repository for the ``principal_preferences`` table."""

    def __init__(self, database_url: str) -> None:
        """Initialize the repository with a database URL."""
        self._database_url = database_url

    def get_preferences(self, principal_id: str) -> dict[str, Any] | None:
        """Return the stored preference row for a principal, or ``None``."""
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT theme, default_reader_view, rows_per_page
                  FROM principal_preferences
                 WHERE principal_id = %s::uuid
                """,
                (principal_id,),
            ).fetchone()

    def upsert_preferences(
        self,
        *,
        principal_id: str,
        theme: str,
        default_reader_view: str,
        rows_per_page: int,
    ) -> None:
        """Insert or update the principal's preference row."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO principal_preferences
                    (principal_id, theme, default_reader_view, rows_per_page, updated_at)
                VALUES (%s::uuid, %s, %s, %s, now())
                ON CONFLICT (principal_id) DO UPDATE
                   SET theme = EXCLUDED.theme,
                       default_reader_view = EXCLUDED.default_reader_view,
                       rows_per_page = EXCLUDED.rows_per_page,
                       updated_at = now()
                """,
                (principal_id, theme, default_reader_view, rows_per_page),
            )

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._database_url, autocommit=True, row_factory=dict_row)
