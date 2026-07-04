"""Postgres instance health report for the doc-ray database.

Complements :mod:`doc_ray.application.corpus_health` (which looks at document/job
*content*) by looking at the Postgres instance itself: size, connection
pressure, long-running or lock-blocked queries, and table bloat. The issue
rules are pure functions over a snapshot, so they are unit-testable without a
database; only :func:`collect` talks to Postgres.

    python -m doc_ray.entrypoints.db_health           # text report
    python -m doc_ray.entrypoints.db_health --json     # machine-readable

Exit codes: ``0`` healthy (info-level findings allowed), ``2`` when any
warning- or error-level issue is found, ``1`` when the report cannot be
produced at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any

import psycopg

from doc_ray.adapters import persistence as db
from doc_ray.config import Config

LONG_QUERY_SECONDS = 60
IDLE_IN_TRANSACTION_SECONDS = 60
CONNECTION_PRESSURE_RATIO = 0.8
DEAD_TUPLE_PCT_WARN = 20.0
MIN_ROWS_FOR_BLOAT_CHECK = 1000

_SIZE_SQL = """
SELECT pg_database_size(current_database()) AS bytes,
       pg_size_pretty(pg_database_size(current_database())) AS pretty
"""

_MAX_CONNECTIONS_SQL = (
    "SELECT setting::int AS max_connections FROM pg_settings WHERE name = 'max_connections'"
)

_CONNECTIONS_SQL = """
SELECT pid, state, wait_event_type,
       extract(epoch FROM (now() - coalesce(state_change, backend_start)))::int AS duration_s,
       left(query, 120) AS query
  FROM pg_stat_activity
 WHERE datname = current_database() AND pid <> pg_backend_pid()
 ORDER BY duration_s DESC NULLS LAST
"""

_TABLES_SQL = """
SELECT relname,
       n_live_tup, n_dead_tup,
       CASE WHEN n_live_tup + n_dead_tup = 0 THEN 0.0
            ELSE round(100.0 * n_dead_tup / (n_live_tup + n_dead_tup), 1) END AS dead_pct
  FROM pg_stat_user_tables
 ORDER BY n_dead_tup DESC
"""


def collect(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """Snapshot Postgres instance health metrics.

    Args:
        conn: Live database connection (dict rows).

    Returns:
        A snapshot dict with keys ``size_bytes``, ``size_pretty``,
        ``max_connections``, ``connections`` (per-backend rows, excluding this
        connection), and ``tables`` (per-table live/dead tuple counts).
    """
    size = conn.execute(_SIZE_SQL).fetchone()
    assert size is not None  # scalar subqueries always return one row
    max_conn = conn.execute(_MAX_CONNECTIONS_SQL).fetchone()
    assert max_conn is not None
    return {
        "size_bytes": size["bytes"],
        "size_pretty": size["pretty"],
        "max_connections": max_conn["max_connections"],
        "connections": [dict(r) for r in conn.execute(_CONNECTIONS_SQL).fetchall()],
        "tables": [dict(r) for r in conn.execute(_TABLES_SQL).fetchall()],
    }


def _connection_issues(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive issues from connection pressure and per-backend state.

    Args:
        snapshot: A snapshot as returned by :func:`collect`.

    Returns:
        Issues found across all connections (possibly empty).
    """
    issues: list[dict[str, Any]] = []
    connections = snapshot["connections"]
    # +1: this analysis connection itself also holds a slot but is excluded
    # from the query (it would otherwise always appear as the top "active" row).
    used = len(connections) + 1
    if used / snapshot["max_connections"] >= CONNECTION_PRESSURE_RATIO:
        issues.append(
            {
                "code": "connection-pressure",
                "severity": "warning",
                "detail": f"{used}/{snapshot['max_connections']} connections in use",
            }
        )
    for c in connections:
        if c["wait_event_type"] == "Lock":
            issues.append(
                {
                    "code": "query-blocked",
                    "severity": "error",
                    "detail": f"pid {c['pid']} blocked waiting on a lock: {c['query']!r}",
                }
            )
        elif c["state"] == "active" and (c["duration_s"] or 0) > LONG_QUERY_SECONDS:
            issues.append(
                {
                    "code": "query-slow",
                    "severity": "warning",
                    "detail": f"pid {c['pid']} active for {c['duration_s']}s: {c['query']!r}",
                }
            )
        elif (
            c["state"] == "idle in transaction"
            and (c["duration_s"] or 0) > IDLE_IN_TRANSACTION_SECONDS
        ):
            issues.append(
                {
                    "code": "idle-in-transaction",
                    "severity": "warning",
                    "detail": f"pid {c['pid']} idle in transaction for {c['duration_s']}s "
                    "— holds locks and blocks vacuum",
                }
            )
    return issues


def _table_issues(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive table-bloat issues from dead-tuple ratios.

    Args:
        snapshot: A snapshot as returned by :func:`collect`.

    Returns:
        Issues found across all tables (possibly empty). Tables below
        ``MIN_ROWS_FOR_BLOAT_CHECK`` rows are skipped to avoid noise on tiny
        tables where a few dead rows swing the percentage wildly.
    """
    issues: list[dict[str, Any]] = []
    for t in snapshot["tables"]:
        rows = t["n_live_tup"] + t["n_dead_tup"]
        if rows >= MIN_ROWS_FOR_BLOAT_CHECK and t["dead_pct"] > DEAD_TUPLE_PCT_WARN:
            issues.append(
                {
                    "code": "table-bloat",
                    "severity": "warning",
                    "detail": f"'{t['relname']}' is {t['dead_pct']}% dead tuples "
                    f"({t['n_dead_tup']:,}/{rows:,} rows) — vacuum is falling behind",
                }
            )
    return issues


def find_issues(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive actionable issues from a health snapshot (pure function).

    Args:
        snapshot: A snapshot as returned by :func:`collect`.

    Returns:
        Issue dicts with ``code``, ``severity`` (``error``/``warning``/``info``),
        and ``detail``.
    """
    return _connection_issues(snapshot) + _table_issues(snapshot)


def exit_code(issues: list[dict[str, Any]]) -> int:
    """Map issues to a process exit code.

    Args:
        issues: Issues as returned by :func:`find_issues`.

    Returns:
        ``2`` when any error- or warning-level issue exists, else ``0``.
        Info-level findings never fail the check.
    """
    return 2 if any(i["severity"] in ("error", "warning") for i in issues) else 0


def render_text(snapshot: dict[str, Any], issues: list[dict[str, Any]]) -> str:
    """Render the snapshot and issues as a human-readable text report.

    Args:
        snapshot: A snapshot as returned by :func:`collect`.
        issues: Issues as returned by :func:`find_issues`.

    Returns:
        The full report as one string.
    """
    lines = ["doc-ray database health", "========================"]
    used = len(snapshot["connections"]) + 1
    lines.append(f"size: {snapshot['size_pretty']} ({snapshot['size_bytes']:,} bytes)")
    lines.append(f"connections: {used}/{snapshot['max_connections']}")
    by_state = Counter(c["state"] for c in snapshot["connections"])
    if by_state:
        lines.append("  " + " ".join(f"{state}={n}" for state, n in by_state.most_common()))

    bloated = [t for t in snapshot["tables"] if t["n_dead_tup"] > 0]
    if bloated:
        lines += ["", f"tables with dead tuples ({len(bloated)}):"]
        for t in bloated[:10]:
            lines.append(
                f"  {t['relname']:24} live={t['n_live_tup']:>10,} "
                f"dead={t['n_dead_tup']:>8,} ({t['dead_pct']}%)"
            )

    lines.append("")
    if not issues:
        lines.append("No issues found.")
    else:
        lines.append(f"issues ({len(issues)}):")
        lines += [f"  [{i['severity']}] {i['code']}: {i['detail']}" for i in issues]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the database health report CLI.

    Args:
        argv: Argument vector; defaults to ``sys.argv`` when ``None``.

    Returns:
        Process exit code: ``0`` healthy, ``2`` issues found, ``1`` when the
        database is unreachable.
    """
    ap = argparse.ArgumentParser(description="Report the health of the doc-ray Postgres instance.")
    ap.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    args = ap.parse_args(argv)

    cfg = Config.from_env()
    try:
        with db.connect(cfg) as conn:
            snapshot = collect(conn)
    except psycopg.OperationalError as e:
        print(f"Cannot reach database: {e}", file=sys.stderr)
        return 1

    issues = find_issues(snapshot)
    if args.json:
        print(json.dumps({**snapshot, "issues": issues}, default=str, indent=2))
    else:
        print(render_text(snapshot, issues))
    return exit_code(issues)


if __name__ == "__main__":
    raise SystemExit(main())
