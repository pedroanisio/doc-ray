"""Pure derivation of data-quality warnings and performance observations.

Every rule here is a function of the typed structure/profile models — no
database access — so the whole rule set is unit-testable without Postgres.
Operational instance health (connection pressure, vacuum/bloat, locks) is out
of scope here; that is :mod:`doc_ray.application.db_health`'s job. This module
looks only at what the *schema and data themselves* reveal.
"""

from __future__ import annotations

from doc_ray.application.db_analyzer.models import Issue, TableProfile, TableStructure

HIGH_NULL_PCT = 50.0
LARGE_TABLE_BYTES = 10 * 1024 * 1024  # 10 MiB: below this, scan choice barely matters.

_RECOMMENDATIONS: dict[str, str] = {
    "missing-primary-key": "Add a primary key to {scope}.",
    "unindexed-foreign-key": "Add an index on {scope} to support its foreign-key lookups.",
    "unused-index": "Confirm {scope} is still needed, or drop it — it has never been scanned.",
    "sequential-scan-heavy": "Add an index on {scope} matching its query pattern; "
    "it is served only by sequential scans.",
    "column-always-null": "Investigate why {scope} is always NULL; consider dropping it "
    "or fixing the write path that should populate it.",
    "high-null-rate": "Investigate the high null rate in {scope}.",
    "constant-column": "Consider removing {scope}; it holds a single repeated value.",
    "duplicate-rows": "Deduplicate {scope}.",
    "numeric-outliers": "Review the outlying values in {scope}.",
}


def _fk_is_indexed(table: TableStructure, fk_columns: list[str]) -> bool:
    """Check whether some index leads with a foreign key's columns.

    This is the standard heuristic query planners rely on: an index is
    useful for a foreign-key lookup only if the FK's columns are its leading
    (left-most) columns, in the same order.

    Args:
        table: The table the foreign key belongs to.
        fk_columns: The foreign key's local columns, in key order.

    Returns:
        Whether an index covers the foreign key's leading columns.
    """
    n = len(fk_columns)
    return any(idx.columns[:n] == fk_columns for idx in table.indexes)


def _structural_issues(table: TableStructure) -> list[Issue]:
    """Derive issues from one table's declared structure alone.

    Args:
        table: The table's structure.

    Returns:
        Issues found (possibly empty).
    """
    scope = f"{table.schema}.{table.name}"
    issues: list[Issue] = []

    if not table.primary_key:
        issues.append(
            Issue("missing-primary-key", "warning", scope, f"{scope} has no primary key", "quality")
        )

    for fk in table.foreign_keys:
        if not _fk_is_indexed(table, fk.columns):
            fk_scope = f"{scope}({', '.join(fk.columns)})"
            issues.append(
                Issue(
                    "unindexed-foreign-key",
                    "warning",
                    fk_scope,
                    f"foreign key {fk.name} on {fk_scope} has no covering index "
                    f"(references {fk.ref_schema}.{fk.ref_table})",
                    "performance",
                )
            )

    for idx in table.indexes:
        if not idx.is_primary and not idx.is_unique and idx.scans == 0:
            idx_scope = f"{scope}.{idx.name}"
            issues.append(
                Issue(
                    "unused-index",
                    "info",
                    idx_scope,
                    f"index {idx.name} on {scope} has never been scanned since stats were "
                    "last reset",
                    "performance",
                )
            )

    if table.seq_scan > 0 and table.idx_scan == 0 and table.size_bytes > LARGE_TABLE_BYTES:
        issues.append(
            Issue(
                "sequential-scan-heavy",
                "warning",
                scope,
                f"{scope} ({table.size_bytes:,} bytes) has {table.seq_scan:,} sequential scans "
                "and 0 index scans",
                "performance",
            )
        )

    return issues


def _profile_issues(profile: TableProfile) -> list[Issue]:
    """Derive issues from one table's data profile.

    Args:
        profile: The table's data profile.

    Returns:
        Issues found (possibly empty).
    """
    scope = f"{profile.schema}.{profile.name}"
    issues: list[Issue] = []

    for col in profile.columns:
        col_scope = f"{scope}.{col.name}"
        if col.row_count > 0 and col.null_count == col.row_count:
            issues.append(
                Issue(
                    "column-always-null",
                    "warning",
                    col_scope,
                    f"{col_scope} is always NULL",
                    "quality",
                )
            )
        elif col.null_pct > HIGH_NULL_PCT:
            issues.append(
                Issue(
                    "high-null-rate",
                    "warning",
                    col_scope,
                    f"{col_scope} is {col.null_pct}% NULL",
                    "quality",
                )
            )
        if col.row_count > col.null_count and col.distinct_count == 1:
            approx = " (estimated)" if col.distinct_estimated else ""
            issues.append(
                Issue(
                    "constant-column",
                    "info",
                    col_scope,
                    f"{col_scope} has exactly one distinct value{approx}",
                    "quality",
                )
            )
        if col.outlier_count:
            issues.append(
                Issue(
                    "numeric-outliers",
                    "info",
                    col_scope,
                    f"{col_scope} has {col.outlier_count:,} value(s) outside the Tukey IQR fence",
                    "quality",
                )
            )

    if profile.duplicate_group_count:
        issues.append(
            Issue(
                "duplicate-rows",
                "warning",
                scope,
                f"{scope} has {profile.duplicate_group_count:,} groups of duplicate rows "
                "(matching on every column)",
                "quality",
            )
        )

    return issues


def find_issues(tables: list[TableStructure], profiles: list[TableProfile]) -> list[Issue]:
    """Derive all data-quality warnings and performance observations.

    Args:
        tables: Every inspected table's structure.
        profiles: Every profiled table's data profile (may be empty when
            structure-only mode was used — profile-derived issues are then
            simply absent).

    Returns:
        All issues, structural issues first, in table order.
    """
    issues: list[Issue] = []
    for table in tables:
        issues.extend(_structural_issues(table))
    for profile in profiles:
        issues.extend(_profile_issues(profile))
    return issues


def exit_code(issues: list[Issue]) -> int:
    """Map issues to a process exit code.

    Args:
        issues: Issues as returned by :func:`find_issues`.

    Returns:
        ``2`` when any error- or warning-level issue exists, else ``0``.
    """
    return 2 if any(i.severity in ("error", "warning") for i in issues) else 0


def recommendations(issues: list[Issue]) -> list[str]:
    """Turn warning/error issues into short, prescriptive next steps.

    Args:
        issues: Issues as returned by :func:`find_issues`.

    Returns:
        One sentence per warning/error issue, in the order the codes were
        first seen; info-level issues (which are observational, not
        actionable) are excluded. Deduplicated by ``(code, scope)``.
    """
    seen: set[tuple[str, str]] = set()
    out: list[str] = []
    for issue in issues:
        if issue.severity == "info":
            continue
        key = (issue.code, issue.scope)
        if key in seen:
            continue
        seen.add(key)
        template = _RECOMMENDATIONS.get(issue.code, "{scope}: " + issue.detail)
        out.append(template.format(scope=issue.scope, detail=issue.detail))
    return out
