"""Typed data structures shared across the db_analyzer package.

Every engine implementation produces these same shapes, so :mod:`quality` and
:mod:`report` never need to know which database engine produced them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ColumnInfo:
    """One column's declared structure.

    Attributes:
        name: Column name.
        data_type: Engine-reported type name (e.g. ``"integer"``, ``"text"``).
        is_nullable: Whether the column accepts NULL.
        default: The column's default expression, if any.
        ordinal_position: 1-based position in the table.
    """

    name: str
    data_type: str
    is_nullable: bool
    default: str | None
    ordinal_position: int


@dataclass(frozen=True)
class IndexInfo:
    """One index on a table.

    Attributes:
        name: Index name.
        columns: Indexed column names, in index key order.
        is_unique: Whether the index enforces uniqueness.
        is_primary: Whether the index backs the primary key.
        definition: The engine's native index-definition text.
        scans: Number of times the index has been used to answer a query
            (0 flags it as a candidate for removal).
    """

    name: str
    columns: list[str]
    is_unique: bool
    is_primary: bool
    definition: str
    scans: int


@dataclass(frozen=True)
class ForeignKeyInfo:
    """One foreign key constraint.

    Attributes:
        name: Constraint name.
        columns: Local column names, in key order.
        ref_schema: Schema of the referenced table.
        ref_table: Name of the referenced table.
        ref_columns: Referenced column names, in the same key order as ``columns``.
    """

    name: str
    columns: list[str]
    ref_schema: str
    ref_table: str
    ref_columns: list[str]


@dataclass(frozen=True)
class ConstraintInfo:
    """One table constraint other than a plain foreign key.

    Attributes:
        name: Constraint name.
        kind: One of ``"primary key"``, ``"unique"``, ``"check"``, ``"foreign key"``.
        definition: The engine's native constraint-definition text.
    """

    name: str
    kind: str
    definition: str


@dataclass(frozen=True)
class TableStructure:
    """Full declared structure of one table.

    Attributes:
        schema: Schema name.
        name: Table name.
        columns: All columns, in ordinal order.
        primary_key: Primary-key column names, in key order (empty if none).
        foreign_keys: Foreign-key constraints defined on this table.
        indexes: Indexes defined on this table.
        constraints: Constraints defined on this table (PK/unique/check/FK).
        size_bytes: Total on-disk size, including indexes and TOAST.
        seq_scan: Sequential scans performed against this table since stats reset.
        idx_scan: Index scans performed against this table since stats reset.
    """

    schema: str
    name: str
    columns: list[ColumnInfo]
    primary_key: list[str]
    foreign_keys: list[ForeignKeyInfo]
    indexes: list[IndexInfo]
    constraints: list[ConstraintInfo]
    size_bytes: int
    seq_scan: int
    idx_scan: int


@dataclass(frozen=True)
class ColumnProfile:
    """Data-level statistics for one column.

    Numeric/date/text fields are populated only when ``data_type`` falls into
    the corresponding bucket; otherwise they stay ``None``.

    Attributes:
        name: Column name.
        data_type: Engine-reported type name.
        row_count: Rows in the table at profiling time.
        null_count: Rows where this column is NULL.
        null_pct: ``null_count / row_count * 100``, rounded to 1 decimal.
        distinct_count: Distinct non-null values, or ``None`` if not computed.
        distinct_estimated: Whether ``distinct_count`` came from the engine's
            planner statistics rather than an exact count (large tables only).
        min_value: Minimum value (numeric or date/time columns).
        max_value: Maximum value (numeric or date/time columns).
        avg_value: Mean value (numeric columns).
        stddev_value: Sample standard deviation (numeric columns).
        min_length: Shortest string length (text columns).
        max_length: Longest string length (text columns).
        avg_length: Mean string length (text columns).
        outlier_count: Values outside the Tukey IQR fence (numeric columns;
            see Tukey, *Exploratory Data Analysis*, 1977, ch. 2).
    """

    name: str
    data_type: str
    row_count: int
    null_count: int
    null_pct: float
    distinct_count: int | None = None
    distinct_estimated: bool = False
    min_value: Any = None
    max_value: Any = None
    avg_value: float | None = None
    stddev_value: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    avg_length: float | None = None
    outlier_count: int | None = None


@dataclass(frozen=True)
class TableProfile:
    """Data-level statistics for one table.

    Attributes:
        schema: Schema name.
        name: Table name.
        row_count: Exact row count at profiling time.
        columns: Per-column profiles.
        duplicate_group_count: Number of distinct whole-row value combinations
            that occur more than once, or ``None`` if the table exceeded the
            duplicate-scan row threshold and was skipped.
    """

    schema: str
    name: str
    row_count: int
    columns: list[ColumnProfile]
    duplicate_group_count: int | None = None


@dataclass(frozen=True)
class Issue:
    """One data-quality warning or performance/indexing observation.

    Attributes:
        code: Short machine-readable identifier (e.g. ``"missing-index"``).
        severity: One of ``"error"``, ``"warning"``, ``"info"``.
        scope: What the issue is about, e.g. ``"public.documents"`` or
            ``"public.documents.language"``.
        detail: Human-readable explanation.
        category: One of ``"quality"`` or ``"performance"`` — separates the
            data-quality-warnings report section from the perf-observations one.
    """

    code: str
    severity: str
    scope: str
    detail: str
    category: str


@dataclass(frozen=True)
class AnalysisResult:
    """The complete output of one analysis run.

    Attributes:
        engine: Engine name (e.g. ``"postgres"``).
        database: Database name as reported by the engine.
        schemas: Schemas that were inspected.
        tables: Structure of every inspected table.
        profiles: Data profile of every inspected table (empty if
            ``--structure-only`` was requested).
        issues: All derived quality warnings and performance observations.
    """

    engine: str
    database: str
    schemas: list[str]
    tables: list[TableStructure]
    profiles: list[TableProfile] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
