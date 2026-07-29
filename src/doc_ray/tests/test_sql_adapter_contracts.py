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
                if not line:
                    continue
                # Skip table-level constraint lines — but match the keyword as a
                # WHOLE first token, not a prefix: `.startswith("CHECK")` wrongly
                # ate the column `checkout_session_id`, silently dropping it from
                # the schema (and from drift protection). Compare the first token.
                first_token = line.split(None, 1)[0].upper()
                if first_token in {
                    "CHECK",
                    "CONSTRAINT",
                    "PRIMARY",
                    "UNIQUE",
                    "FOREIGN",
                    "EXCLUDE",
                }:
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


# ---------------------------------------------------------------------------
# Repo-wide adapter SQL guard (drift-map M1): generalize the two curated checks
# above to EVERY raw-SQL adapter, auto-deriving each query's alias->table map
# from its own FROM/JOIN clauses so no per-function map is needed. Source files
# are parsed as text (never imported) so optional heavy deps stay out of scope.
# ---------------------------------------------------------------------------

_ADAPTERS_DIR = _ROOT / "src" / "doc_ray" / "adapters"

#: Tokens that follow FROM/JOIN but are keywords, not table aliases.
_ALIAS_STOPWORDS = {
    "as",
    "on",
    "and",
    "or",
    "using",
    "left",
    "right",
    "inner",
    "outer",
    "full",
    "cross",
    "join",
    "from",
    "where",
    "select",
    "not",
    "is",
    "null",
    "in",
    "order",
    "by",
    "group",
    "having",
    "limit",
    "offset",
    "asc",
    "desc",
    "distinct",
    "case",
    "when",
    "then",
    "else",
    "end",
    "with",
    "lateral",
    "union",
    "all",
    "values",
    "returning",
    "set",
    "into",
}


def _sql_literals_in(directory: Path) -> dict[str, list[str]]:
    """Return SQL string literals in every Python module below one directory."""
    out: dict[str, list[str]] = {}
    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(v in node.value.upper() for v in ("SELECT", "INSERT", "UPDATE", "DELETE"))
            and (
                "FROM" in node.value.upper()
                or "JOIN" in node.value.upper()
                or node.value.upper().strip().startswith("UPDATE")
            )
        ]
        if literals:
            out[str(path.relative_to(_ROOT))] = literals
    return out


def _adapter_sql_literals() -> dict[str, list[str]]:
    """Every SQL string literal in each adapter module, keyed by relative path."""
    return _sql_literals_in(_ADAPTERS_DIR)


def _alias_to_table(sql: str, known_tables: set[str]) -> dict[str, str]:
    """Map each ``FROM/JOIN <table> [AS] <alias>`` to its table (schema tables only).

    CTEs, subqueries, and functions either don't match ``<name> <alias>`` or
    resolve to a name absent from the schema, so they are skipped — no false
    positives (verified: 942 refs across 42 adapters, 0 spurious).
    """
    amap: dict[str, str] = {}
    for table, alias in re.findall(
        r"(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)\s+(?:AS\s+)?([a-z_][a-z0-9_]*)", sql, re.IGNORECASE
    ):
        if alias.lower() in _ALIAS_STOPWORDS:
            continue
        if table in known_tables:
            amap[alias] = table
    return amap


def _qualified_column_failures(
    sources: dict[str, list[str]], columns: dict[str, set[str]]
) -> tuple[list[str], int]:
    """Resolve qualified SQL columns and return stale references plus scan size."""
    known = set(columns)
    failures: list[str] = []
    checked = 0
    for filename, literals in sources.items():
        for sql in literals:
            aliases = _alias_to_table(sql, known)
            for alias, column in re.findall(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b", sql):
                table = aliases.get(alias)
                if table is None:
                    continue
                checked += 1
                if column not in columns[table]:
                    failures.append(f"{filename}: {alias}.{column} not in {table}")
    return sorted(set(failures)), checked


def test_all_adapter_sql_qualified_columns_exist_in_schema():
    """Every ``alias.column`` in every adapter's SQL must exist in the schema.

    Broadens the two curated guards above to all raw-SQL adapters, with the table
    alias auto-derived from each query's FROM/JOIN (drift-map M1). A renamed or
    removed column an adapter still names becomes a test failure instead of a
    runtime error on an uncovered query path.
    """
    failures, checked = _qualified_column_failures(_adapter_sql_literals(), _schema_columns())
    assert not failures, (
        f"adapter SQL references column(s) absent from the migrated schema: {sorted(set(failures))}"
    )
    assert checked >= 200, f"only {checked} alias.column refs checked — the scanner regressed"


def test_all_webapp_sql_qualified_columns_exist_in_schema():
    """Every qualified webapp read-path column must exist in the migrated schema."""
    sources = _sql_literals_in(_ROOT / "webapp")

    failures, checked = _qualified_column_failures(sources, _schema_columns())

    assert not failures, f"webapp SQL drifted from migrations: {failures}"
    assert len(sources) >= 14, "the webapp SQL inventory shrank without updating this guard"
    assert checked >= 300, f"only {checked} webapp alias.column refs checked — scanner regressed"


def test_qualified_column_guard_rejects_a_removed_schema_column():
    """Regression: an unresolved qualified column must make the guard fail loudly."""
    sources = {"broken.py": ["SELECT d.removed_title FROM documents AS d"]}

    failures, checked = _qualified_column_failures(sources, _schema_columns())

    assert checked == 1
    assert failures == ["broken.py: d.removed_title not in documents"]


def test_schema_columns_keeps_columns_named_like_a_constraint_keyword():
    """Regression: a column whose name begins with a constraint keyword survives.

    ``_schema_columns`` once skipped any body line ``.startswith(("CHECK", ...))``,
    which ate ``tenant_subscriptions.checkout_session_id`` — silently dropping it
    from the schema and from drift protection. The victim column must be present.
    """
    columns = _schema_columns()
    assert "checkout_session_id" in columns.get("tenant_subscriptions", set())


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
