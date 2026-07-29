"""Regression guards for postgres_exporter SQL/schema drift (drift-map C1)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from test_sql_adapter_contracts import (
    _ALIAS_STOPWORDS,
    _SQL_KEYWORDS,
    _alias_to_table,
    _schema_columns,
)

_ROOT = Path(__file__).resolve().parents[3]
_POSTGRES_EXPORTER_QUERIES = _ROOT / "config" / "observability" / "postgres_queries.yaml"


def _split_top_level_csv(value: str) -> list[str]:
    """Split a SQL projection list without splitting function arguments."""
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return [part for part in parts if part]


def _top_level_select_outputs(sql: str) -> set[str]:
    """Return explicit output names from the outermost SELECT projection."""
    match = re.search(r"\bSELECT\b", sql, flags=re.IGNORECASE)
    if match is None:
        return set()

    depth = 0
    projection_end: int | None = None
    for index in range(match.end(), len(sql)):
        char = sql[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and re.match(r"FROM\b", sql[index:], flags=re.IGNORECASE):
            projection_end = index
            break
    projection = sql[match.end() : projection_end]

    outputs: set[str] = set()
    for expression in _split_top_level_csv(projection):
        alias = re.search(r"\bAS\s+([a-z_][a-z0-9_]*)\s*$", expression, flags=re.IGNORECASE)
        if alias is not None:
            outputs.add(alias.group(1).lower())
            continue
        direct_column = re.fullmatch(
            r"\s*(?:[a-z_][a-z0-9_]*\.)?([a-z_][a-z0-9_]*)\s*",
            expression,
            flags=re.IGNORECASE,
        )
        if direct_column is not None:
            outputs.add(direct_column.group(1).lower())
    return outputs


def _exporter_query_schema_failures(
    spec: object, schema_columns: dict[str, set[str]]
) -> tuple[list[str], int]:
    """Validate postgres_exporter query tables, columns, and declared outputs."""
    if not isinstance(spec, dict):
        return ["postgres exporter query spec must be a mapping"], 0

    sql_keywords = (
        _SQL_KEYWORDS
        | _ALIAS_STOPWORDS
        | {
            "epoch",
            "false",
            "group",
            "having",
            "true",
            "when",
        }
    )
    failures: list[str] = []
    checked = 0

    for query_name, config in sorted(spec.items()):
        if not isinstance(query_name, str) or not isinstance(config, dict):
            failures.append(f"{query_name}: query configuration must be a mapping")
            continue
        query = config.get("query")
        metrics = config.get("metrics")
        if not isinstance(query, str) or not isinstance(metrics, list):
            failures.append(f"{query_name}: query must be text and metrics must be a list")
            continue

        # String values are data (status labels, interval literals), never
        # schema identifiers. Removing them keeps the static scan conservative.
        sql = re.sub(r"'(?:''|[^'])*'", " ", query.lower())
        referenced_tables = sorted(set(re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)\b", sql)))
        known_tables = [table for table in referenced_tables if table in schema_columns]
        for table in referenced_tables:
            if table not in schema_columns:
                failures.append(f"{query_name}: table {table} is absent from the migrated schema")

        output_aliases = set(re.findall(r"\bas\s+([a-z_][a-z0-9_]*)\b", sql))
        function_names = set(re.findall(r"\b([a-z_][a-z0-9_]*)\s*\(", sql))
        table_aliases = set(_alias_to_table(sql, set(schema_columns)))
        identifiers = set(re.findall(r"\b[a-z_][a-z0-9_]*\b", sql))
        candidates = sorted(
            identifiers
            - sql_keywords
            - set(referenced_tables)
            - output_aliases
            - function_names
            - table_aliases
        )

        for column in candidates:
            if not known_tables:
                continue
            checked += 1
            matching_tables = [table for table in known_tables if column in schema_columns[table]]
            if matching_tables:
                continue
            if len(known_tables) == 1:
                failures.append(
                    f"{query_name}: {known_tables[0]}.{column} is absent from the migrated schema"
                )
            else:
                failures.append(
                    f"{query_name}: {column} is absent from referenced tables "
                    f"{', '.join(known_tables)}"
                )

        select_outputs = _top_level_select_outputs(sql)
        metric_names = {
            name.lower()
            for metric in metrics
            if isinstance(metric, dict)
            for name in metric
            if isinstance(name, str)
        }
        for metric_name in sorted(metric_names - select_outputs):
            failures.append(f"{query_name}: metric {metric_name} is absent from the SELECT output")

    return sorted(set(failures)), checked


def test_postgres_exporter_queries_reference_live_schema_columns():
    """Every exporter query must keep naming migrated tables and columns."""
    spec = yaml.safe_load(_POSTGRES_EXPORTER_QUERIES.read_text(encoding="utf-8"))

    failures, checked = _exporter_query_schema_failures(spec, _schema_columns())

    assert not failures, f"postgres_exporter SQL drifted from migrations: {failures}"
    assert len(spec) >= 6, "the exporter-query inventory shrank without updating this guard"
    assert checked >= 13, f"only {checked} exporter column refs checked — the scanner regressed"


def test_postgres_exporter_guard_rejects_missing_table_column_and_metric():
    """Regression: the guard must fail loudly instead of passing vacuously."""
    spec = {
        "missing_column": {
            "query": "SELECT removed_progress AS progress FROM jobs",
            "metrics": [{"progress": {"usage": "GAUGE"}}],
        },
        "missing_table": {
            "query": "SELECT status FROM jobs_archive",
            "metrics": [{"status": {"usage": "LABEL"}}],
        },
        "missing_metric_output": {
            "query": "SELECT status FROM jobs",
            "metrics": [{"kind": {"usage": "LABEL"}}],
        },
    }

    failures, checked = _exporter_query_schema_failures(spec, _schema_columns())

    assert checked == 2
    assert failures == [
        "missing_column: jobs.removed_progress is absent from the migrated schema",
        "missing_metric_output: metric kind is absent from the SELECT output",
        "missing_table: table jobs_archive is absent from the migrated schema",
    ]
