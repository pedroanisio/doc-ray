"""PostgreSQL implementation of the :class:`~.base.Engine` interface.

Structure comes from ``information_schema`` and ``pg_catalog`` (the
``pg_constraint``/``pg_index`` catalogs are used directly for foreign keys and
indexes, since the classic ``information_schema`` join loses column order on
composite keys). Data profiling issues one bounded aggregate query per
statistic — never a row-by-row scan in Python — and all dynamic identifiers
(schema/table/column names) are composed with :mod:`psycopg.sql` rather than
string interpolation, so the tool cannot be tricked into executing anything
other than a read against the named object.

Distinct-value counts fall back to Postgres's own planner statistics
(``pg_stats.n_distinct``) above ``sample_threshold`` rather than an exact
``COUNT(DISTINCT ...)`` scan; see the Postgres documentation for
``pg_stats`` (https://www.postgresql.org/docs/current/view-pg-stats.html) for
the sign convention used in :func:`_estimate_distinct`. Outlier detection uses
Tukey's IQR fence (Tukey, *Exploratory Data Analysis*, 1977, ch. 2): a value is
an outlier if it falls outside ``[Q1 - 1.5*IQR, Q3 + 1.5*IQR]``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from doc_ray.application.db_analyzer.engines.base import Engine
from doc_ray.application.db_analyzer.models import (
    ColumnInfo,
    ColumnProfile,
    ConstraintInfo,
    ForeignKeyInfo,
    IndexInfo,
    TableProfile,
    TableStructure,
)

_NUMERIC_TYPES = {
    "smallint",
    "integer",
    "bigint",
    "numeric",
    "decimal",
    "real",
    "double precision",
}
_DATE_TYPES = {
    "date",
    "timestamp without time zone",
    "timestamp with time zone",
    "time without time zone",
    "time with time zone",
}
_TEXT_TYPES = {"text", "character varying", "character", "citext"}

_CONTYPE_KIND = {"p": "primary key", "u": "unique", "c": "check", "f": "foreign key"}

_SCHEMAS_SQL = """
SELECT schema_name FROM information_schema.schemata
 WHERE schema_name NOT IN ('pg_catalog', 'information_schema')
   AND schema_name NOT LIKE 'pg_toast%'
   AND schema_name NOT LIKE 'pg_temp%'
 ORDER BY schema_name
"""

_TABLES_SQL = """
SELECT table_name FROM information_schema.tables
 WHERE table_schema = %s AND table_type = 'BASE TABLE'
 ORDER BY table_name
"""

_COLUMNS_SQL = """
SELECT column_name, data_type, is_nullable, column_default, ordinal_position
  FROM information_schema.columns
 WHERE table_schema = %s AND table_name = %s
 ORDER BY ordinal_position
"""

_PK_SQL = """
SELECT kcu.column_name
  FROM information_schema.table_constraints tc
  JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
 WHERE tc.table_schema = %s AND tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'
 ORDER BY kcu.ordinal_position
"""

# pg_catalog (not information_schema) so composite-key column order is exact.
_FK_SQL = """
SELECT con.conname AS name, a.attname AS column_name,
       nsp2.nspname AS ref_schema, cl2.relname AS ref_table, af.attname AS ref_column
  FROM pg_constraint con
  JOIN pg_class cl ON cl.oid = con.conrelid
  JOIN pg_namespace nsp ON nsp.oid = cl.relnamespace
  JOIN pg_class cl2 ON cl2.oid = con.confrelid
  JOIN pg_namespace nsp2 ON nsp2.oid = cl2.relnamespace
  JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS uk(attnum, ord) ON true
  JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS fk(attnum, ord) ON fk.ord = uk.ord
  JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = uk.attnum
  JOIN pg_attribute af ON af.attrelid = con.confrelid AND af.attnum = fk.attnum
 WHERE con.contype = 'f' AND nsp.nspname = %s AND cl.relname = %s
 ORDER BY con.conname, uk.ord
"""

_INDEX_SQL = """
SELECT i.relname AS index_name, ix.indisunique AS is_unique, ix.indisprimary AS is_primary,
       pg_get_indexdef(ix.indexrelid) AS definition, a.attname AS column_name, k.ord AS ord,
       COALESCE(psi.idx_scan, 0) AS scans
  FROM pg_index ix
  JOIN pg_class t ON t.oid = ix.indrelid
  JOIN pg_class i ON i.oid = ix.indexrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
  JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
  JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
  LEFT JOIN pg_stat_user_indexes psi ON psi.indexrelid = ix.indexrelid
 WHERE n.nspname = %s AND t.relname = %s
 ORDER BY i.relname, k.ord
"""

_CONSTRAINTS_SQL = """
SELECT c.conname AS name, c.contype AS contype, pg_get_constraintdef(c.oid) AS definition
  FROM pg_constraint c
  JOIN pg_class t ON t.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = %s AND t.relname = %s
 ORDER BY c.conname
"""

_SIZE_SQL = (
    "SELECT pg_total_relation_size(format('%%I.%%I', %s::text, %s::text)::regclass) AS size_bytes"
)

_SCAN_SQL = """
SELECT COALESCE(seq_scan, 0) AS seq_scan, COALESCE(idx_scan, 0) AS idx_scan
  FROM pg_stat_user_tables
 WHERE schemaname = %s AND relname = %s
"""


def _classify(data_type: str) -> str:
    """Bucket a Postgres type name for profiling purposes.

    Args:
        data_type: The ``information_schema.columns.data_type`` value.

    Returns:
        One of ``"numeric"``, ``"date"``, ``"text"``, ``"other"``.
    """
    if data_type in _NUMERIC_TYPES:
        return "numeric"
    if data_type in _DATE_TYPES:
        return "date"
    if data_type in _TEXT_TYPES:
        return "text"
    return "other"


def _estimate_distinct(n_distinct: float, row_count: int) -> int:
    """Convert ``pg_stats.n_distinct`` into an absolute distinct-value count.

    Per the Postgres documentation for ``pg_stats``: a positive value is a
    direct estimate of the number of distinct values; a negative value is the
    negative of the ratio of distinct values to rows, used when the distinct
    count is expected to scale with table size.

    Args:
        n_distinct: The raw ``pg_stats.n_distinct`` value.
        row_count: The table's row count, used to resolve a ratio.

    Returns:
        An absolute estimated distinct-value count.
    """
    return int(n_distinct) if n_distinct >= 0 else round(-n_distinct * row_count)


def _one_row(
    conn: psycopg.Connection[Any],
    query: str | sql.Composed,
    params: tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    """Execute a query guaranteed to return exactly one row, and return it.

    Every call site here is a bare aggregate (``count``, ``min``/``max``, ...)
    with no ``GROUP BY``, so this always holds.

    Args:
        conn: A connection returned by :meth:`PostgresEngine.connect`.
        query: A query string or composed :class:`psycopg.sql.Composed`.
        params: Bind parameters, if any.

    Returns:
        The single result row.
    """
    row: dict[str, Any] | None = conn.execute(query, params).fetchone()
    assert row is not None  # a bare aggregate always returns exactly one row
    return row


class PostgresEngine(Engine):
    """Engine backend for PostgreSQL (and PostgreSQL-compatible databases)."""

    name = "postgres"

    @contextmanager
    def connect(self, dsn: str, *, read_only: bool) -> Iterator[psycopg.Connection[Any]]:
        """Open a dict-row Postgres connection, optionally DB-enforced read-only.

        Runs without ``autocommit``: psycopg only attaches ``SET TRANSACTION
        READ ONLY`` to an explicit transaction, so ``read_only`` would be
        silently unenforced under autocommit. Every analyzer query is a read,
        so the whole session shares one transaction that is always rolled
        back on exit — belt-and-braces on top of the read-only flag itself.

        Args:
            dsn: A ``postgresql://`` connection string.
            read_only: When ``True``, sets the session's ``read_only`` flag
                *before* any transaction starts, so Postgres itself — not
                just this tool's code — rejects any write statement.

        Yields:
            An open connection with a ``dict_row`` row factory.
        """
        conn = psycopg.connect(dsn, row_factory=dict_row)
        try:
            if read_only:
                conn.read_only = True
            yield conn
        finally:
            conn.rollback()
            conn.close()

    def database_name(self, conn: psycopg.Connection[Any]) -> str:
        """Return the name of the connected database.

        Args:
            conn: A connection returned by :meth:`connect`.

        Returns:
            The database name.
        """
        row = conn.execute("SELECT current_database() AS name").fetchone()
        assert row is not None  # SELECT with no FROM always returns exactly one row
        return str(row["name"])

    def list_schemas(self, conn: psycopg.Connection[Any]) -> list[str]:
        """List user (non-system) schemas.

        Args:
            conn: A connection returned by :meth:`connect`.

        Returns:
            Schema names, in a stable order.
        """
        return [r["schema_name"] for r in conn.execute(_SCHEMAS_SQL).fetchall()]

    def list_tables(self, conn: psycopg.Connection[Any], schema: str) -> list[str]:
        """List base tables in a schema.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.

        Returns:
            Table names, in a stable order.
        """
        return [r["table_name"] for r in conn.execute(_TABLES_SQL, (schema,)).fetchall()]

    def get_table_structure(
        self, conn: psycopg.Connection[Any], schema: str, table: str
    ) -> TableStructure:
        """Describe one table's declared structure.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.

        Returns:
            The table's columns, keys, indexes, constraints, and size.
        """
        columns = [
            ColumnInfo(
                name=r["column_name"],
                data_type=r["data_type"],
                is_nullable=(r["is_nullable"] == "YES"),
                default=r["column_default"],
                ordinal_position=r["ordinal_position"],
            )
            for r in conn.execute(_COLUMNS_SQL, (schema, table)).fetchall()
        ]
        primary_key = [r["column_name"] for r in conn.execute(_PK_SQL, (schema, table)).fetchall()]
        foreign_keys = self._foreign_keys(conn, schema, table)
        indexes = self._indexes(conn, schema, table)
        constraints = [
            ConstraintInfo(
                name=r["name"],
                kind=_CONTYPE_KIND.get(r["contype"], str(r["contype"])),
                definition=r["definition"],
            )
            for r in conn.execute(_CONSTRAINTS_SQL, (schema, table)).fetchall()
        ]
        size_row = conn.execute(_SIZE_SQL, (schema, table)).fetchone()
        scan_row = conn.execute(_SCAN_SQL, (schema, table)).fetchone()
        return TableStructure(
            schema=schema,
            name=table,
            columns=columns,
            primary_key=primary_key,
            foreign_keys=foreign_keys,
            indexes=indexes,
            constraints=constraints,
            size_bytes=(size_row["size_bytes"] or 0) if size_row else 0,
            seq_scan=scan_row["seq_scan"] if scan_row else 0,
            idx_scan=scan_row["idx_scan"] if scan_row else 0,
        )

    def _foreign_keys(
        self, conn: psycopg.Connection[Any], schema: str, table: str
    ) -> list[ForeignKeyInfo]:
        """Group raw FK column rows into one :class:`ForeignKeyInfo` per constraint.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.

        Returns:
            One entry per foreign-key constraint, columns in key order.
        """
        grouped: dict[str, dict[str, Any]] = {}
        for r in conn.execute(_FK_SQL, (schema, table)).fetchall():
            g = grouped.setdefault(
                r["name"],
                {
                    "ref_schema": r["ref_schema"],
                    "ref_table": r["ref_table"],
                    "columns": [],
                    "ref_columns": [],
                },
            )
            g["columns"].append(r["column_name"])
            g["ref_columns"].append(r["ref_column"])
        return [
            ForeignKeyInfo(
                name=name,
                columns=g["columns"],
                ref_schema=g["ref_schema"],
                ref_table=g["ref_table"],
                ref_columns=g["ref_columns"],
            )
            for name, g in grouped.items()
        ]

    def _indexes(self, conn: psycopg.Connection[Any], schema: str, table: str) -> list[IndexInfo]:
        """Group raw index column rows into one :class:`IndexInfo` per index.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.

        Returns:
            One entry per index, columns in key order. Expression-index
            columns (which have no backing ``pg_attribute`` entry) are
            omitted from the column list but the index itself is still listed.
        """
        grouped: dict[str, dict[str, Any]] = {}
        for r in conn.execute(_INDEX_SQL, (schema, table)).fetchall():
            g = grouped.setdefault(
                r["index_name"],
                {
                    "is_unique": r["is_unique"],
                    "is_primary": r["is_primary"],
                    "definition": r["definition"],
                    "scans": r["scans"],
                    "columns": [],
                },
            )
            g["columns"].append(r["column_name"])
        return [
            IndexInfo(
                name=name,
                columns=g["columns"],
                is_unique=g["is_unique"],
                is_primary=g["is_primary"],
                definition=g["definition"],
                scans=g["scans"],
            )
            for name, g in grouped.items()
        ]

    def profile_table(
        self,
        conn: psycopg.Connection[Any],
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
            columns: Declared columns (from :meth:`get_table_structure`).
            primary_key: Primary-key column names, excluded from the
                duplicate-row scan (see :meth:`Engine.profile_table`).
            sample_threshold: Row-count above which distinct-value counts use
                planner statistics instead of an exact scan.
            duplicate_threshold: Row-count above which the whole-row
                duplicate scan is skipped.

        Returns:
            Row count, per-column statistics, and duplicate-row findings.
        """
        row_count = self._row_count(conn, schema, table)
        profiles = [
            self._profile_column(conn, schema, table, col, row_count, sample_threshold)
            for col in columns
        ]
        content_columns = [c.name for c in columns if c.name not in primary_key]
        dup_count = (
            self._duplicate_group_count(conn, schema, table, content_columns)
            if content_columns and row_count <= duplicate_threshold
            else None
        )
        return TableProfile(
            schema=schema,
            name=table,
            row_count=row_count,
            columns=profiles,
            duplicate_group_count=dup_count,
        )

    def _row_count(self, conn: psycopg.Connection[Any], schema: str, table: str) -> int:
        """Count rows in a table exactly.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.

        Returns:
            The exact row count.
        """
        q = sql.SQL("SELECT count(*) AS n FROM {t}").format(t=sql.Identifier(schema, table))
        return int(_one_row(conn, q)["n"])

    def _profile_column(
        self,
        conn: psycopg.Connection[Any],
        schema: str,
        table: str,
        col: ColumnInfo,
        row_count: int,
        sample_threshold: int,
    ) -> ColumnProfile:
        """Compute null rate, distinct count, and type-specific statistics for one column.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.
            col: The column's declared structure.
            row_count: The table's exact row count.
            sample_threshold: Row-count above which distinct-value counts use
                planner statistics instead of an exact scan.

        Returns:
            The column's data profile.
        """
        non_null_q = sql.SQL("SELECT count({c}) AS n FROM {t}").format(
            c=sql.Identifier(col.name), t=sql.Identifier(schema, table)
        )
        non_null = int(_one_row(conn, non_null_q)["n"])
        null_count = row_count - non_null
        null_pct = round(100.0 * null_count / row_count, 1) if row_count else 0.0

        distinct_count: int | None = None
        distinct_estimated = False
        if non_null:
            distinct_count, distinct_estimated = self._distinct_count(
                conn, schema, table, col.name, row_count, sample_threshold
            )

        min_value = max_value = avg_value = stddev_value = None
        min_length = max_length = avg_length = None
        outlier_count: int | None = None
        kind = _classify(col.data_type)

        if non_null and kind == "numeric":
            stats = self._numeric_stats(conn, schema, table, col.name)
            min_value, max_value, avg_value, stddev_value = (
                stats["lo"],
                stats["hi"],
                stats["avg"],
                stats["sd"],
            )
            if stats["q1"] is not None and stats["q3"] is not None:
                outlier_count = self._numeric_outlier_count(
                    conn, schema, table, col.name, stats["q1"], stats["q3"]
                )
        elif non_null and kind == "date":
            stats = self._date_stats(conn, schema, table, col.name)
            min_value, max_value = stats["lo"], stats["hi"]
        elif non_null and kind == "text":
            stats = self._text_stats(conn, schema, table, col.name)
            min_length, max_length, avg_length = stats["lo"], stats["hi"], stats["avg"]

        return ColumnProfile(
            name=col.name,
            data_type=col.data_type,
            row_count=row_count,
            null_count=null_count,
            null_pct=null_pct,
            distinct_count=distinct_count,
            distinct_estimated=distinct_estimated,
            min_value=min_value,
            max_value=max_value,
            avg_value=avg_value,
            stddev_value=stddev_value,
            min_length=min_length,
            max_length=max_length,
            avg_length=avg_length,
            outlier_count=outlier_count,
        )

    def _distinct_count(
        self,
        conn: psycopg.Connection[Any],
        schema: str,
        table: str,
        column: str,
        row_count: int,
        sample_threshold: int,
    ) -> tuple[int | None, bool]:
        """Count distinct non-null values, exactly below the threshold and by estimate above it.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.
            column: Column name.
            row_count: The table's exact row count.
            sample_threshold: Row-count above which the estimate is used.

        Returns:
            ``(count, estimated)``. ``count`` is ``None`` if Postgres has no
            statistics for the column yet (run ``ANALYZE``).
        """
        if row_count <= sample_threshold:
            q = sql.SQL("SELECT count(DISTINCT {c}) AS n FROM {t}").format(
                c=sql.Identifier(column), t=sql.Identifier(schema, table)
            )
            return int(_one_row(conn, q)["n"]), False
        row = conn.execute(
            "SELECT n_distinct FROM pg_stats WHERE schemaname=%s AND tablename=%s AND attname=%s",
            (schema, table, column),
        ).fetchone()
        if row is None or row["n_distinct"] is None:
            return None, True
        return _estimate_distinct(row["n_distinct"], row_count), True

    def _numeric_stats(
        self, conn: psycopg.Connection[Any], schema: str, table: str, column: str
    ) -> dict[str, Any]:
        """Compute min/max/mean/stddev/quartiles for a numeric column.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.
            column: Column name.

        Returns:
            A row with ``lo``, ``hi``, ``avg``, ``sd``, ``q1``, ``q3``.
        """
        q = sql.SQL(
            "SELECT min({c}) AS lo, max({c}) AS hi, avg({c})::float8 AS avg, "
            "stddev_samp({c})::float8 AS sd, "
            "percentile_cont(0.25) WITHIN GROUP (ORDER BY {c}) AS q1, "
            "percentile_cont(0.75) WITHIN GROUP (ORDER BY {c}) AS q3 "
            "FROM {t} WHERE {c} IS NOT NULL"
        ).format(c=sql.Identifier(column), t=sql.Identifier(schema, table))
        return _one_row(conn, q)

    def _numeric_outlier_count(
        self,
        conn: psycopg.Connection[Any],
        schema: str,
        table: str,
        column: str,
        q1: float,
        q3: float,
    ) -> int:
        """Count values outside the Tukey IQR fence ``[Q1 - 1.5*IQR, Q3 + 1.5*IQR]``.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.
            column: Column name.
            q1: First quartile.
            q3: Third quartile.

        Returns:
            Count of non-null values outside the fence.
        """
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        q = sql.SQL(
            "SELECT count(*) AS n FROM {t} WHERE {c} IS NOT NULL AND ({c} < %s OR {c} > %s)"
        ).format(t=sql.Identifier(schema, table), c=sql.Identifier(column))
        return int(_one_row(conn, q, (lo, hi))["n"])

    def _date_stats(
        self, conn: psycopg.Connection[Any], schema: str, table: str, column: str
    ) -> dict[str, Any]:
        """Compute min/max for a date/timestamp column.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.
            column: Column name.

        Returns:
            A row with ``lo`` and ``hi``.
        """
        q = sql.SQL("SELECT min({c}) AS lo, max({c}) AS hi FROM {t} WHERE {c} IS NOT NULL").format(
            c=sql.Identifier(column), t=sql.Identifier(schema, table)
        )
        return _one_row(conn, q)

    def _text_stats(
        self, conn: psycopg.Connection[Any], schema: str, table: str, column: str
    ) -> dict[str, Any]:
        """Compute min/max/mean string length for a text column.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.
            column: Column name.

        Returns:
            A row with ``lo``, ``hi``, ``avg`` (all lengths in characters).
        """
        q = sql.SQL(
            "SELECT min(length({c})) AS lo, max(length({c})) AS hi, "
            "avg(length({c}))::float8 AS avg FROM {t} WHERE {c} IS NOT NULL"
        ).format(c=sql.Identifier(column), t=sql.Identifier(schema, table))
        return _one_row(conn, q)

    def _duplicate_group_count(
        self, conn: psycopg.Connection[Any], schema: str, table: str, column_names: list[str]
    ) -> int:
        """Count distinct whole-row value combinations that occur more than once.

        Args:
            conn: A connection returned by :meth:`connect`.
            schema: Schema name.
            table: Table name.
            column_names: All column names, used to group on the whole row.

        Returns:
            Number of duplicate groups (not the total redundant row count).
        """
        cols = sql.SQL(", ").join(sql.Identifier(c) for c in column_names)
        q = sql.SQL(
            "SELECT count(*) AS n FROM (SELECT 1 FROM {t} GROUP BY {cols} HAVING count(*) > 1) dup"
        ).format(t=sql.Identifier(schema, table), cols=cols)
        return int(_one_row(conn, q)["n"])
