"""List broken pipeline state: stuck/failed jobs and empty/unannotated documents.

Reuses ``webapp.operator_health.build_operator_health`` as the single source of
truth for what counts as "broken" (the same signals the ``/operator/health``
page renders) instead of re-deriving those rules here, plus one supplementary
check that page does not have yet: jobs stuck in ``queued`` because no worker
picked them up (the single-worker-replica queue-backlog failure mode).

Read-only: every query is a ``SELECT``, nothing here writes to the database.

Usage:
    python scripts/pipeline_health_check.py
    python scripts/pipeline_health_check.py --json
    python scripts/pipeline_health_check.py --database-url postgresql://...

Exit code: 0 clean, 1 warnings only, 2 any error-severity issue found, 3 could
not connect (usually $DATABASE_URL isn't set — see the hint the script prints).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webapp.operator_health import (  # noqa: E402
    SEVERITIES,
    Query,
    QueryOne,
    _issue,
    _issue_groups,
    build_operator_health,
)

#: How long a job may sit ``queued`` before it is worth flagging. Matches
#: WORKER_CLAIM_TIMEOUT_SECONDS' default (src/doc_ray/entrypoints/worker.py)
#: so this fires around the same time a slow job would otherwise be reclaimed.
QUEUE_BACKLOG_SECONDS = 120


def _queue_backlog_issues(job_spool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag queued jobs no worker has claimed within QUEUE_BACKLOG_SECONDS.

    build_operator_health() already flags stalled/failed/reclaimed jobs, but
    nothing flags a healthy-looking queue that is simply backed up behind one
    slow job with no spare worker capacity to pick up the rest.
    """
    issues = []
    for job in job_spool:
        if job.get("status") != "queued":
            continue
        age = job.get("queued_age_s")
        if age is None or age <= QUEUE_BACKLOG_SECONDS:
            continue
        title = job.get("document_title")
        suffix = f" (document {title!r})" if title else ""
        issues.append(
            _issue(
                "queue-backlog",
                "warning",
                f"{job.get('kind')} job {job.get('id')} has been queued {age}s "
                f"with no worker available{suffix}",
                document_id=job.get("document_id"),
            )
        )
    return issues


def collect(q: Query, q1: QueryOne) -> dict[str, Any]:
    """Return the operator-health report plus the supplementary queue-backlog check."""
    report = build_operator_health(q, q1)
    corpus = report["corpus"]
    corpus["issues"] = corpus["issues"] + _queue_backlog_issues(corpus.get("job_spool", []))
    corpus["issue_groups"] = _issue_groups(corpus["issues"])
    return report


def _all_issues(report: dict[str, Any]) -> list[dict[str, Any]]:
    return list(report["corpus"]["issues"]) + list(report["database"]["issues"])


def _print_report(report: dict[str, Any]) -> None:
    sections = (
        ("Pipeline / corpus", report["corpus"]["issues"]),
        ("Database", report["database"]["issues"]),
    )
    total = sum(len(issues) for _, issues in sections)
    if not total:
        print("No broken pipeline state found.")
        return

    print(f"{total} issue(s) found\n")
    for label, issues in sections:
        if not issues:
            continue
        print(f"== {label} ==")
        for severity in SEVERITIES:
            for issue in issues:
                if issue["severity"] != severity:
                    continue
                doc_id = issue.get("document_id")
                suffix = f" [doc {doc_id}]" if doc_id else ""
                print(f"  [{severity:7}] {issue['code']}: {issue['detail']}{suffix}")
        print()


def _connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


def main(argv: list[str] | None = None) -> int:
    """Run the check from the CLI; return a process exit code (0/1/2)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", "postgresql://docray:docray@localhost:5455/docray"),
        help="Postgres DSN (defaults to $DATABASE_URL).",
    )
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    args = parser.parse_args(argv)

    try:
        conn = _connect(args.database_url)
    except psycopg.OperationalError as exc:
        if not os.environ.get("DATABASE_URL"):
            print(
                "Couldn't connect to Postgres, and $DATABASE_URL is not set — this "
                "script fell back to a default DSN with a placeholder password, which "
                "the real dev stack refuses. Run:\n"
                "  set -a && source .env && set +a && python3 scripts/pipeline_health_check.py\n"
                f"(underlying error: {exc.args[0].splitlines()[0] if exc.args else exc})",
                file=sys.stderr,
            )
        else:
            print(
                f"Couldn't connect to Postgres at the given --database-url: {exc}", file=sys.stderr
            )
        return 3

    with conn:

        def q(sql: str) -> list[dict[str, Any]]:
            return conn.execute(sql).fetchall()

        def q1(sql: str) -> dict[str, Any] | None:
            return conn.execute(sql).fetchone()

        report = collect(q, q1)

    if args.json:
        print(json.dumps(report, default=str, indent=2))
    else:
        _print_report(report)

    issues = _all_issues(report)
    if any(issue["severity"] == "error" for issue in issues):
        return 2
    if any(issue["severity"] == "warning" for issue in issues):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
