"""Stable JSON contract for the operator health surface."""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "operator-health.v1"
TOTAL_KEYS = (
    "documents",
    "sentences",
    "tokens",
    "entity_mentions",
    "entity_links",
    "canonical_entries",
    "lexemes",
    "embeddings",
)


def operator_health_payload(report: dict[str, Any]) -> dict[str, Any]:
    """Return the stable operator-health JSON payload."""
    corpus = report["corpus"]
    database = report["database"]
    return {
        "schema_version": SCHEMA_VERSION,
        "corpus": {
            "totals": {key: _int(corpus["totals"].get(key)) for key in TOTAL_KEYS},
            "issues": [_issue(issue) for issue in corpus["issues"]],
            "issue_groups": [_group(group) for group in corpus["issue_groups"]],
            "jobs": [
                {
                    "kind": str(row.get("kind") or "unknown"),
                    "status": str(row.get("status") or "unknown"),
                    "count": _int(row.get("count")),
                }
                for row in corpus["jobs"]
            ],
            "job_spool": [_job(row) for row in corpus["job_spool"]],
        },
        "database": {
            "size": {
                "bytes": _int(database.get("size_bytes")),
                "pretty": str(database.get("size_pretty") or "unknown"),
            },
            "connections": {
                "used": len(database["connections"]) + 1,
                "max": _int(database.get("max_connections")),
                "states": [
                    {"state": str(state), "count": _int(count)}
                    for state, count in database["connection_state_counts"]
                ],
            },
            "tables": [
                {
                    "name": str(row.get("relname") or "unknown"),
                    "live_rows": _int(row.get("n_live_tup")),
                    "dead_rows": _int(row.get("n_dead_tup")),
                    "dead_pct": float(row.get("dead_pct") or 0),
                }
                for row in database["tables"]
            ],
            "issues": [_issue(issue) for issue in database["issues"]],
            "issue_groups": [_group(group) for group in database["issue_groups"]],
        },
    }


def _issue(issue: dict[str, Any]) -> dict[str, Any]:
    item = {
        "code": str(issue.get("code") or "unknown"),
        "severity": str(issue.get("severity") or "info"),
        "detail": str(issue.get("detail") or ""),
    }
    if issue.get("document_id"):
        item["document_id"] = str(issue["document_id"])
    return item


def _job(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "kind": str(row.get("kind") or "unknown"),
        "status": str(row.get("status") or "unknown"),
        "attempts": _int(row.get("attempts")),
        "max_attempts": _int(row.get("max_attempts")),
        "worker": row.get("locked_by"),
        "document_id": row.get("document_id"),
        "document_title": row.get("document_title"),
        "progress": {
            "current": _nullable_int(row.get("progress_current")),
            "total": _nullable_int(row.get("progress_total")),
            "stage": row.get("progress_stage"),
        },
        "runtime_seconds": _nullable_int(row.get("runtime_s")),
        "heartbeat_age_seconds": _nullable_int(row.get("heartbeat_age_s")),
        "queued_age_seconds": _nullable_int(row.get("queued_age_s")),
        "reclaim_after_seconds": _nullable_int(row.get("reclaim_after_s")),
        "last_error": str(row.get("error") or ""),
    }


def _group(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "severity": str(group.get("severity") or "info"),
        "count": len(group.get("findings") or []),
        "codes": [str(issue.get("code") or "unknown") for issue in group.get("findings") or []],
    }


def _int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _nullable_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
