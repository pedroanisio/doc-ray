"""Regression guards for raw SQL adapter/schema drift."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from doc_ray.adapters import persistence_read, queue_postgres

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATIONS = _ROOT / "db" / "migrations"
_SQL_KEYWORDS = {
    "all",
    "and",
    "any",
    "as",
    "asc",
    "by",
    "case",
    "coalesce",
    "desc",
    "for",
    "from",
    "greatest",
    "in",
    "insert",
    "interval",
    "into",
    "is",
    "join",
    "limit",
    "not",
    "now",
    "null",
    "or",
    "order",
    "returning",
    "select",
    "set",
    "text",
    "then",
    "ulid",
    "update",
    "uuid",
    "values",
    "where",
    "with",
}


def _schema_columns() -> dict[str, set[str]]:
    columns: dict[str, set[str]] = {}
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        for table, body in re.findall(
            r"CREATE TABLE IF NOT EXISTS ([a-z_]+)\s*\((.*?)\n\);",
            sql,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            table_cols = columns.setdefault(table, set())
            for raw_line in body.splitlines():
                line = raw_line.strip().rstrip(",")
                if not line or line.upper().startswith(("CHECK", "CONSTRAINT", "PRIMARY")):
                    continue
                match = re.match(r"([a-z_][a-z0-9_]*)\s+", line)
                if match:
                    table_cols.add(match.group(1))
        for table, body in re.findall(
            r"ALTER TABLE ([a-z_]+)\s+(.*?);",
            sql,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            table_cols = columns.setdefault(table, set())
            table_cols.update(
                re.findall(
                    r"ADD COLUMN(?: IF NOT EXISTS)?\s+([a-z_][a-z0-9_]*)",
                    body,
                    flags=re.IGNORECASE,
                )
            )
            table_cols.difference_update(
                re.findall(
                    r"DROP COLUMN(?: IF EXISTS)?\s+([a-z_][a-z0-9_]*)",
                    body,
                    flags=re.IGNORECASE,
                )
            )
    return columns


def _assert_qualified_columns_exist(fn: object, aliases: dict[str, str]) -> None:
    source = inspect.getsource(fn)
    columns = _schema_columns()
    failures: list[str] = []
    for alias, column in re.findall(r"\b([a-z][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b", source):
        table = aliases.get(alias)
        if table is None:
            continue
        if column not in columns[table]:
            failures.append(f"{fn.__name__}: {alias}.{column} not in {table}")
    assert not failures


def test_persistence_read_document_sql_qualified_columns_exist_in_migrations():
    """Document read-adapter SQL must not reference stale qualified columns."""
    _assert_qualified_columns_exist(
        persistence_read.list_documents,
        {
            "d": "documents",
            "s": "sentences",
            "p": "document_provenance",
            "dt": "document_tags",
            "t": "tags",
        },
    )
    _assert_qualified_columns_exist(
        persistence_read.list_sentences,
        {"s": "sentences", "sp": "spans"},
    )


def test_queue_postgres_job_alias_columns_exist_in_migrations():
    """Queue claim SQL must not reference stale jobs columns through aliases."""
    _assert_qualified_columns_exist(queue_postgres.claim, {"j": "jobs"})


def test_queue_postgres_unqualified_job_sql_columns_exist_in_migrations():
    """Queue SQL strings must not carry stale unqualified jobs columns."""
    jobs_columns = _schema_columns()["jobs"]
    sql_text = "\n".join(_queue_postgres_sql_literals())
    sql_text = re.sub(r"--.*", "", sql_text)
    sql_text = re.sub(r"'[^']*'", "", sql_text)
    # psycopg named placeholders (%(doc)s) carry a parameter name, not a column
    # name — strip them before scanning, or every named-parameter query trips
    # this check on its own placeholders.
    sql_text = re.sub(r"%\([a-z_][a-z0-9_]*\)s", "", sql_text, flags=re.IGNORECASE)
    identifiers = set(re.findall(r"\b[a-z_][a-z0-9_]*\b", sql_text.lower()))
    allowed = (
        _SQL_KEYWORDS
        | jobs_columns
        | {
            "attempt",
            "canceled",
            "docray_job_events",
            "eligible",
            "false",
            "id",
            "j",
            "jobs",
            "locked",
            "picked",
            "queued",
            "running",
            "s",
            "skip",
            "succeeded",
            "worker_id",
        }
    )
    stale = sorted(
        identifier
        for identifier in identifiers
        if identifier not in allowed and not identifier.endswith("_kinds")
    )

    assert not stale


def _queue_postgres_sql_literals() -> list[str]:
    literals = [queue_postgres._INSERT, queue_postgres._CLAIM, queue_postgres._SELECT]
    for fn in (
        queue_postgres.status_async,
        queue_postgres.is_canceled,
        queue_postgres.cancel,
        queue_postgres.cancel_async,
        queue_postgres.list_async,
        queue_postgres.heartbeat,
        queue_postgres.request_drain,
        queue_postgres.update_progress,
        queue_postgres.complete,
        queue_postgres.fail,
        queue_postgres.reap_stale,
    ):
        literals.extend(_sql_literals_from_function(fn))
    return [value for value in literals if "jobs" in value]


def _sql_literals_from_function(fn: object) -> list[str]:
    tree = ast.parse(inspect.getsource(fn))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and any(verb in node.value.upper() for verb in ("SELECT", "UPDATE", "INSERT"))
    ]
