"""The engine interface every database backend must implement.

A concrete engine owns everything database-specific: opening a read-only
connection, listing schemas/tables, describing structure, and profiling data.
Everything above this layer (:mod:`quality`, :mod:`report`, :mod:`run`) works
only with the typed models in :mod:`doc_ray.application.db_analyzer.models`
and never touches SQL directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from typing import Any

from doc_ray.application.db_analyzer.models import ColumnInfo, TableProfile, TableStructure


class Engine(ABC):
    """Abstract base for a pluggable database backend."""

    name: str

    @abstractmethod
    def connect(self, dsn: str, *, read_only: bool) -> AbstractContextManager[Any]:
        """Open a connection to the database.

        Args:
            dsn: Connection string in the engine's native format.
            read_only: When ``True``, the connection must be opened such that
                the database itself rejects any write — not merely "the code
                doesn't issue writes". This is the tool's default-safe mode.

        Returns:
            A context manager yielding an engine-specific connection object,
            closing it on exit.
        """

    @abstractmethod
    def database_name(self, conn: Any) -> str:
        """Return the name of the connected database.

        Args:
            conn: A connection returned by :meth:`connect`.

        Returns:
            The database name as reported by the engine.
        """

    @abstractmethod
    def list_schemas(self, conn: Any) -> list[str]:
        """List user (non-system) schemas.

        Args:
            conn: A connection returned by :meth:`connect`.

        Returns:
            Schema names, in a stable order.
        """

    @abstractmethod
    def list_tables(self, conn: Any, schema: str) -> list[str]:
        """List base tables in a schema.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.

        Returns:
            Table names, in a stable order. Views are excluded.
        """

    @abstractmethod
    def get_table_structure(self, conn: Any, schema: str, table: str) -> TableStructure:
        """Describe one table's declared structure.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.

        Returns:
            The table's columns, keys, indexes, constraints, and size.
        """

    @abstractmethod
    def profile_table(
        self,
        conn: Any,
        schema: str,
        table: str,
        columns: list[ColumnInfo],
        primary_key: list[str],
        *,
        sample_threshold: int,
        duplicate_threshold: int,
    ) -> TableProfile:
        """Profile one table's data.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.
            columns: Declared columns to profile (from :meth:`get_table_structure`),
                so the engine knows each column's type without re-querying it.
            primary_key: The table's primary-key column names. Excluded from
                the whole-row duplicate scan — a surrogate key makes every row
                trivially "unique," hiding real content-level duplicates.
            sample_threshold: Row-count above which distinct-value counts use
                the engine's planner statistics instead of an exact scan.
            duplicate_threshold: Row-count above which the whole-row
                duplicate scan is skipped.

        Returns:
            Row count, per-column statistics, and duplicate-row findings.
        """
