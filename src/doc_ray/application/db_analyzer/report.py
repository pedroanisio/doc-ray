"""Render an :class:`AnalysisResult` as Markdown and JSON reports.

The generated report is a production/operational artifact (tool output, not
authored documentation), so it carries no authorship disclaimer of its own.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from doc_ray.application.db_analyzer.models import AnalysisResult, ColumnProfile, Issue
from doc_ray.application.db_analyzer.quality import recommendations


def to_json(result: AnalysisResult) -> str:
    """Render the analysis as an indented JSON document.

    Args:
        result: The completed analysis.

    Returns:
        A JSON string. Non-JSON-native values the database driver may return
        (dates, ``Decimal``) are rendered via ``str()``.
    """
    payload: dict[str, Any] = asdict(result)
    payload["recommendations"] = recommendations(result.issues)
    return json.dumps(payload, default=str, indent=2)


def _fmt_bytes(n: int) -> str:
    """Render a byte count in human-readable units.

    Args:
        n: Byte count.

    Returns:
        e.g. ``"12.3 MB"``.
    """
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _overview_section(result: AnalysisResult) -> list[str]:
    """Render the database-overview section.

    Args:
        result: The completed analysis.

    Returns:
        Markdown lines.
    """
    total_bytes = sum(t.size_bytes for t in result.tables)
    return [
        "## Database overview",
        "",
        f"- **Engine:** {result.engine}",
        f"- **Database:** {result.database}",
        f"- **Schemas inspected:** {', '.join(result.schemas) or '(none)'}",
        f"- **Tables inspected:** {len(result.tables)}",
        f"- **Total size:** {_fmt_bytes(total_bytes)}",
        "",
    ]


def _schema_inventory_section(result: AnalysisResult) -> list[str]:
    """Render the schema-inventory table (one row per table).

    Args:
        result: The completed analysis.

    Returns:
        Markdown lines.
    """
    lines = [
        "## Schema inventory",
        "",
        "| Table | Columns | Rows | Size | Primary key | FKs | Indexes |",
        "|---|---|---|---|---|---|---|",
    ]
    profiles_by_key = {(p.schema, p.name): p for p in result.profiles}
    for t in result.tables:
        p = profiles_by_key.get((t.schema, t.name))
        rows = f"{p.row_count:,}" if p else "—"
        pk = ", ".join(t.primary_key) if t.primary_key else "—"
        lines.append(
            f"| {t.schema}.{t.name} | {len(t.columns)} | {rows} | {_fmt_bytes(t.size_bytes)} | "
            f"{pk} | {len(t.foreign_keys)} | {len(t.indexes)} |"
        )
    lines.append("")
    return lines


def _column_stat_line(col: ColumnProfile) -> str:
    """Summarize one column's profile as a single table cell.

    Args:
        col: The column's data profile.

    Returns:
        A comma-separated summary of whichever statistics apply to its type.
    """
    parts = [f"null={col.null_pct}%"]
    if col.distinct_count is not None:
        approx = "~" if col.distinct_estimated else ""
        parts.append(f"distinct={approx}{col.distinct_count:,}")
    if col.min_value is not None or col.max_value is not None:
        parts.append(f"range=[{col.min_value}, {col.max_value}]")
    if col.avg_value is not None:
        parts.append(f"avg={col.avg_value:.3g}")
    if col.stddev_value is not None:
        parts.append(f"stddev={col.stddev_value:.3g}")
    if col.avg_length is not None:
        parts.append(f"len=[{col.min_length}..{col.max_length}] avg={col.avg_length:.1f}")
    if col.outlier_count:
        parts.append(f"outliers={col.outlier_count:,}")
    return ", ".join(parts)


def _table_profile_section(result: AnalysisResult) -> list[str]:
    """Render the table-by-table profiling and column-level statistics sections.

    Args:
        result: The completed analysis.

    Returns:
        Markdown lines; empty if no profiles were collected (structure-only mode).
    """
    if not result.profiles:
        return []
    lines = ["## Table-by-table profiling", ""]
    for p in result.profiles:
        lines.append(f"### {p.schema}.{p.name}")
        lines.append("")
        lines.append(f"- **Rows:** {p.row_count:,}")
        if p.duplicate_group_count is not None:
            lines.append(f"- **Duplicate row groups:** {p.duplicate_group_count:,}")
        else:
            lines.append(
                "- **Duplicate row groups:** not checked (exceeds the duplicate-scan threshold)"
            )
        lines += ["", "| Column | Type | Stats |", "|---|---|---|"]
        lines += [f"| {c.name} | {c.data_type} | {_column_stat_line(c)} |" for c in p.columns]
        lines.append("")
    return lines


def _relationships_section(result: AnalysisResult) -> list[str]:
    """Render the detected-relationships section from every table's foreign keys.

    Args:
        result: The completed analysis.

    Returns:
        Markdown lines.
    """
    rows = [
        f"- {t.schema}.{t.name}({', '.join(fk.columns)}) → "
        f"{fk.ref_schema}.{fk.ref_table}({', '.join(fk.ref_columns)})"
        for t in result.tables
        for fk in t.foreign_keys
    ]
    lines = ["## Detected relationships", ""]
    lines += rows if rows else ["(no foreign keys found)"]
    lines.append("")
    return lines


def _issues_section(title: str, issues: list[Issue]) -> list[str]:
    """Render one issues section (quality warnings or perf observations).

    Args:
        title: Section heading text.
        issues: Issues belonging to this section.

    Returns:
        Markdown lines.
    """
    lines = [f"## {title}", ""]
    if not issues:
        lines.append("None found.")
    else:
        lines += [f"- **[{i.severity}]** `{i.code}` — {i.detail}" for i in issues]
    lines.append("")
    return lines


def _recommendations_section(result: AnalysisResult) -> list[str]:
    """Render the recommended-next-steps section.

    Args:
        result: The completed analysis.

    Returns:
        Markdown lines.
    """
    recs = recommendations(result.issues)
    lines = ["## Recommended next steps", ""]
    if recs:
        lines += [f"{i}. {r}" for i, r in enumerate(recs, start=1)]
    else:
        lines.append("No action items found.")
    lines.append("")
    return lines


def to_markdown(result: AnalysisResult) -> str:
    """Render the analysis as a Markdown report.

    Args:
        result: The completed analysis.

    Returns:
        The full report as one Markdown string, with sections for the
        database overview, schema inventory, table-by-table profiling and
        column statistics, detected relationships, data quality warnings,
        performance/indexing observations, and recommended next steps.
    """
    quality_issues = [i for i in result.issues if i.category == "quality"]
    perf_issues = [i for i in result.issues if i.category == "performance"]
    lines = ["# Database analysis report", ""]
    lines += _overview_section(result)
    lines += _schema_inventory_section(result)
    lines += _table_profile_section(result)
    lines += _relationships_section(result)
    lines += _issues_section("Data quality warnings", quality_issues)
    lines += _issues_section("Performance & indexing observations", perf_issues)
    lines += _recommendations_section(result)
    return "\n".join(lines)
