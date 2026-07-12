"""Read-only operator health snapshots for the Flask browser.

This module intentionally stays inside ``webapp`` and imports nothing from
``doc_ray``. It mirrors the read-only SQL and issue rules used by the health
entrypoints so the browser can render the same operational signals without
joining the application hexagon.
"""

from __future__ import annotations

import hmac
import os
from collections import Counter
from collections.abc import Callable
from typing import Any

from flask import abort, current_app, request, session

Query = Callable[[str], list[dict[str, Any]]]
QueryOne = Callable[[str], dict[str, Any] | None]

OPERATOR_HEALTH_TOKEN = os.environ.get("DOCRAY_OPERATOR_HEALTH_TOKEN") or None
STALE_HEARTBEAT_SECONDS = 300
LONG_QUERY_SECONDS = 60
IDLE_IN_TRANSACTION_SECONDS = 60
CONNECTION_PRESSURE_RATIO = 0.8
DEAD_TUPLE_PCT_WARN = 20.0
MIN_ROWS_FOR_BLOAT_CHECK = 1000
SEVERITIES = ("error", "warning", "info")

_CORPUS_TOTALS_SQL = """
SELECT (SELECT count(*) FROM documents)         AS documents,
       (SELECT count(*) FROM sentences)         AS sentences,
       (SELECT count(*) FROM tokens)            AS tokens,
       (SELECT count(*) FROM entity_mention)    AS entity_mentions,
       (SELECT count(*) FROM entity_link)       AS entity_links,
       (SELECT count(*) FROM canonical)         AS canonical_entries,
       (SELECT count(*) FROM lexemes)           AS lexemes,
       (SELECT count(*) FROM embedding)         AS embeddings
"""

_DOCUMENTS_SQL = """
SELECT d.id, d.title, d.language, d.status,
       (SELECT count(*) FROM sentences s WHERE s.document_id = d.id) AS sentences,
       (SELECT count(*) FROM entity_mention em
          JOIN annotations a ON em.annotation_id = a.id
         WHERE a.document_id = d.id) AS mentions,
       (SELECT count(*) FROM entity_link el
          JOIN entity_mention em ON el.mention_id = em.id
          JOIN annotations a ON em.annotation_id = a.id
         WHERE a.document_id = d.id) AS links
  FROM documents d
 ORDER BY d.created_at
"""

_JOBS_SQL = """
SELECT kind, status, count(*) AS count
  FROM jobs
 GROUP BY kind, status
 ORDER BY kind, status
"""

_RUNNING_JOBS_SQL = """
SELECT id, kind, locked_by,
       extract(epoch FROM (now() - coalesce(heartbeat_at, started_at, created_at)))::int
           AS heartbeat_age_s
  FROM jobs
 WHERE status = 'running'
"""

_JOB_SPOOL_SQL = f"""
SELECT j.id,
       j.kind,
       j.status,
       j.attempts,
       j.max_attempts,
       j.locked_by,
       j.document_id,
       d.title AS document_title,
       j.progress_current,
       j.progress_total,
       j.progress_stage,
       CASE WHEN j.started_at IS NULL THEN NULL
            ELSE extract(epoch FROM (now() - j.started_at))::int END AS runtime_s,
       CASE WHEN j.heartbeat_at IS NULL THEN NULL
            ELSE extract(epoch FROM (now() - j.heartbeat_at))::int END AS heartbeat_age_s,
       extract(epoch FROM (now() - j.created_at))::int AS queued_age_s,
       CASE WHEN j.status = 'running' AND j.heartbeat_at IS NOT NULL
            THEN greatest(
                0,
                {STALE_HEARTBEAT_SECONDS}
                - extract(epoch FROM (now() - j.heartbeat_at))::int
            )
            ELSE NULL END AS reclaim_after_s,
       left(coalesce(j.error, ''), 240) AS error
  FROM jobs j
  LEFT JOIN documents d ON d.id = j.document_id
 WHERE j.status IN ('queued', 'running', 'failed')
    OR coalesce(j.error, '') <> ''
 ORDER BY CASE j.status
            WHEN 'running' THEN 0
            WHEN 'queued' THEN 1
            WHEN 'failed' THEN 2
            ELSE 3
          END,
          j.created_at
 LIMIT 40
"""

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


def require_operator_health() -> None:
    """Hide the operator health page unless the configured token is supplied.

    Accepts the token via the ``X-Operator-Token`` header, which never lands in
    URLs/access logs/browser history. A query-string ``?token=`` is still
    accepted as a one-time bootstrap link (e.g. pasted from a runbook), but
    instead of requiring the URL to keep carrying it, a valid bootstrap sets a
    signed session flag (Flask's session cookie, already signed by
    ``app.secret_key``) so every subsequent request in that browser session
    authenticates from the cookie, not the query string (BL-107).
    """
    configured = current_app.config.get("OPERATOR_HEALTH_TOKEN") or OPERATOR_HEALTH_TOKEN
    if not configured:
        abort(404)
    header_token = request.headers.get("X-Operator-Token")
    query_token = request.args.get("token")
    for supplied in (header_token, query_token):
        if supplied is not None and hmac.compare_digest(str(supplied), str(configured)):
            session["operator_health_ok"] = True
            return
    if session.get("operator_health_ok"):
        return
    abort(404)


def build_operator_health(q: Query, q1: QueryOne) -> dict[str, Any]:
    """Build the combined corpus and database health report for the browser."""
    corpus = _collect_corpus(q, q1)
    database = _collect_database(q, q1)
    corpus["issues"] = _corpus_issues(corpus)
    database["issues"] = _database_issues(database)
    corpus["issue_groups"] = _issue_groups(corpus["issues"])
    database["issue_groups"] = _issue_groups(database["issues"])
    return {"corpus": corpus, "database": database}


def _collect_corpus(q: Query, q1: QueryOne) -> dict[str, Any]:
    totals = dict(q1(_CORPUS_TOTALS_SQL) or {})
    documents = [dict(row) for row in q(_DOCUMENTS_SQL)]
    jobs = [dict(row) for row in q(_JOBS_SQL)]
    running_jobs = [dict(row) for row in q(_RUNNING_JOBS_SQL)]
    job_spool = [_job_spool_row(row) for row in q(_JOB_SPOOL_SQL)]
    return {
        "totals": totals,
        "documents": documents,
        "jobs": jobs,
        "running_jobs": running_jobs,
        "job_spool": job_spool,
    }


def _collect_database(q: Query, q1: QueryOne) -> dict[str, Any]:
    size = dict(q1(_SIZE_SQL) or {})
    max_conn = dict(q1(_MAX_CONNECTIONS_SQL) or {})
    connections = [dict(row) for row in q(_CONNECTIONS_SQL)]
    tables = [dict(row) for row in q(_TABLES_SQL)]
    state_counts = Counter(str(row.get("state") or "unknown") for row in connections)
    return {
        "size_bytes": _int(size.get("bytes")),
        "size_pretty": size.get("pretty") or "unknown",
        "max_connections": max(_int(max_conn.get("max_connections")), 1),
        "connections": connections,
        "connection_state_counts": state_counts.most_common(),
        "tables": tables,
    }


def _corpus_issues(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for doc in snapshot["documents"]:
        issues.extend(_document_issues(doc))
    for row in snapshot["jobs"]:
        if row.get("status") == "failed":
            issues.append(
                _issue(
                    "job-failed",
                    "error",
                    f"{row.get('count', 0)} failed {row.get('kind')} job(s) in the queue",
                )
            )
    for job in snapshot["running_jobs"]:
        if _int(job.get("heartbeat_age_s")) > STALE_HEARTBEAT_SECONDS:
            issues.append(
                _issue(
                    "job-stalled",
                    "warning",
                    f"{job.get('kind')} job {job.get('id')} "
                    f"(worker {job.get('locked_by')}) last heartbeat "
                    f"{job.get('heartbeat_age_s')}s ago",
                )
            )
    for job in snapshot.get("job_spool", []):
        if job.get("status") == "running" and _int(job.get("attempts")) >= _int(
            job.get("max_attempts")
        ):
            issues.append(
                _issue(
                    "job-last-attempt",
                    "warning",
                    f"{job.get('kind')} job {job.get('id')} is on attempt "
                    f"{job.get('attempts')}/{job.get('max_attempts')}: "
                    f"{job.get('progress_stage') or 'no progress stage'}",
                    document_id=job.get("document_id"),
                )
            )
        if "reclaimed:" in str(job.get("error") or ""):
            issues.append(
                _issue(
                    "job-reclaimed",
                    "warning",
                    f"{job.get('kind')} job {job.get('id')} was reclaimed after "
                    f"a stale heartbeat; now {job.get('status')}",
                    document_id=job.get("document_id"),
                )
            )
    totals = snapshot["totals"]
    if _int(totals.get("sentences")) > 0 and _int(totals.get("embeddings")) == 0:
        issues.append(
            _issue(
                "embeddings-missing",
                "info",
                "corpus has sentences but no embeddings; vector search unavailable",
            )
        )
    return issues


def _document_issues(doc: dict[str, Any]) -> list[dict[str, Any]]:
    if doc.get("status") == "extracted" and _int(doc.get("sentences")) == 0:
        return [
            _issue(
                "document-empty",
                "error",
                f"'{doc.get('title')}' is extracted but has 0 sentences",
                document_id=doc.get("id"),
            )
        ]

    issues: list[dict[str, Any]] = []
    if _int(doc.get("sentences")) > 0 and doc.get("language") is None:
        issues.append(
            _issue(
                "fts-degraded",
                "warning",
                f"'{doc.get('title')}' has language NULL; full-text search uses fallback config",
                document_id=doc.get("id"),
            )
        )
    if _int(doc.get("sentences")) > 0 and _int(doc.get("mentions")) == 0:
        issues.append(
            _issue(
                "document-unannotated",
                "warning",
                f"'{doc.get('title')}' has {doc.get('sentences')} sentences but no entity mentions",
                document_id=doc.get("id"),
            )
        )
    return issues


def _database_issues(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    connections = snapshot["connections"]
    used = len(connections) + 1
    if used / snapshot["max_connections"] >= CONNECTION_PRESSURE_RATIO:
        issues.append(
            _issue(
                "connection-pressure",
                "warning",
                f"{used}/{snapshot['max_connections']} connections in use",
            )
        )

    for row in connections:
        if row.get("wait_event_type") == "Lock":
            issues.append(
                _issue(
                    "query-blocked",
                    "error",
                    f"pid {row.get('pid')} blocked waiting on a lock: {row.get('query')!r}",
                )
            )
        elif row.get("state") == "active" and _int(row.get("duration_s")) > LONG_QUERY_SECONDS:
            issues.append(
                _issue(
                    "query-slow",
                    "warning",
                    f"pid {row.get('pid')} active for {row.get('duration_s')}s: "
                    f"{row.get('query')!r}",
                )
            )
        elif (
            row.get("state") == "idle in transaction"
            and _int(row.get("duration_s")) > IDLE_IN_TRANSACTION_SECONDS
        ):
            issues.append(
                _issue(
                    "idle-in-transaction",
                    "warning",
                    f"pid {row.get('pid')} idle in transaction for {row.get('duration_s')}s",
                )
            )

    for table in snapshot["tables"]:
        rows = _int(table.get("n_live_tup")) + _int(table.get("n_dead_tup"))
        if (
            rows >= MIN_ROWS_FOR_BLOAT_CHECK
            and float(table.get("dead_pct") or 0) > DEAD_TUPLE_PCT_WARN
        ):
            issues.append(
                _issue(
                    "table-bloat",
                    "warning",
                    f"'{table.get('relname')}' is {table.get('dead_pct')}% dead tuples "
                    f"({table.get('n_dead_tup'):,}/{rows:,} rows)",
                )
            )
    return issues


def _job_spool_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["id"] = str(item.get("id") or "")
    item["kind"] = str(item.get("kind") or "unknown")
    item["status"] = str(item.get("status") or "unknown")
    item["attempts"] = _int(item.get("attempts"))
    item["max_attempts"] = _int(item.get("max_attempts"))
    doc = item.get("document_id")
    item["document_id"] = str(doc) if doc is not None else None
    item["document_title"] = item.get("document_title") or None
    item["locked_by"] = item.get("locked_by") or None
    item["progress_current"] = _nullable_int(item.get("progress_current"))
    item["progress_total"] = _nullable_int(item.get("progress_total"))
    item["progress_stage"] = item.get("progress_stage") or None
    item["runtime_s"] = _nullable_int(item.get("runtime_s"))
    item["heartbeat_age_s"] = _nullable_int(item.get("heartbeat_age_s"))
    item["queued_age_s"] = _nullable_int(item.get("queued_age_s"))
    item["reclaim_after_s"] = _nullable_int(item.get("reclaim_after_s"))
    item["error"] = str(item.get("error") or "")
    return item


def _issue(code: str, severity: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "detail": detail, **extra}


def _issue_groups(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"severity": severity, "findings": [i for i in issues if i["severity"] == severity]}
        for severity in SEVERITIES
    ]


def _int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _nullable_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
