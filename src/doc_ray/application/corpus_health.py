"""Corpus health report over the doc-ray Postgres store.

Collects a snapshot of corpus metrics (document coverage, job queue state,
FTS readiness) and derives actionable issues from it. The derivation rules
are pure functions over the snapshot, so they are unit-testable without a
database; only :func:`collect` talks to Postgres.

    python -m doc_ray.entrypoints.corpus_health           # text report
    python -m doc_ray.entrypoints.corpus_health --json    # machine-readable

Exit codes: ``0`` healthy (info-level findings allowed), ``2`` when any
warning- or error-level issue is found, ``1`` when the report cannot be
produced at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import psycopg

from doc_ray.adapters import persistence as db
from doc_ray.config import Config

STALE_HEARTBEAT_SECONDS = 300

_TOTALS_SQL = """
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
         WHERE a.document_id = d.id) AS links,
       -- Kinds that failed for good: every attempt spent, and no later run of the
       -- same kind ever succeeded. Nothing re-enqueues a failed job, so each of
       -- these is a pipeline stage that will stay undone until an operator acts.
       (SELECT array_agg(DISTINCT j.kind ORDER BY j.kind)
          FROM jobs j
         WHERE j.document_id = d.id
           AND j.status = 'failed'
           AND j.attempts >= j.max_attempts
           AND NOT EXISTS (SELECT 1 FROM jobs ok
                            WHERE ok.document_id = j.document_id
                              AND ok.kind = j.kind
                              AND ok.status = 'succeeded')) AS stalled_kinds
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


def collect(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """Snapshot corpus health metrics from the database.

    Args:
        conn: Live database connection (dict rows).

    Returns:
        A snapshot dict with keys ``totals`` (corpus-wide counts),
        ``documents`` (per-document coverage rows), ``jobs`` (kind/status
        counts), and ``running_jobs`` (in-flight jobs with heartbeat age).
    """
    totals = conn.execute(_TOTALS_SQL).fetchone()
    assert totals is not None  # scalar subqueries always return one row
    return {
        "totals": dict(totals),
        "documents": [dict(r) for r in conn.execute(_DOCUMENTS_SQL).fetchall()],
        "jobs": [dict(r) for r in conn.execute(_JOBS_SQL).fetchall()],
        "running_jobs": [dict(r) for r in conn.execute(_RUNNING_JOBS_SQL).fetchall()],
    }


def _document_issues(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive issues for one document coverage row.

    Args:
        doc: One entry of the snapshot's ``documents`` list.

    Returns:
        Issues found for this document (possibly empty).
    """
    stalled = doc.get("stalled_kinds") or []
    if stalled:
        # Reported before the emptiness check below, and as an error rather than a
        # warning: "annotation never ran" reads as a stage still queued, when in
        # fact the queue is empty and no path leads back to this document.
        return [
            {
                "code": "pipeline-stalled",
                "severity": "error",
                "document_id": doc["id"],
                "detail": (
                    f"'{doc['title']}' has exhausted every attempt at "
                    f"{', '.join(stalled)} and no queued work remains — the chain "
                    "stops here until the stage is re-enqueued"
                ),
            }
        ]
    if doc["status"] == "extracted" and doc["sentences"] == 0:
        return [
            {
                "code": "document-empty",
                "severity": "error",
                "document_id": doc["id"],
                "detail": f"'{doc['title']}' is extracted but has 0 sentences",
            }
        ]
    issues: list[dict[str, Any]] = []
    if doc["sentences"] > 0 and doc["language"] is None:
        issues.append(
            {
                "code": "fts-degraded",
                "severity": "warning",
                "document_id": doc["id"],
                "detail": (
                    f"'{doc['title']}' has language NULL: its FTS vector uses the 'simple' "
                    "config while queries use a language config — reannotate to fix"
                ),
            }
        )
    if doc["sentences"] > 0 and doc["mentions"] == 0:
        issues.append(
            {
                "code": "document-unannotated",
                "severity": "warning",
                "document_id": doc["id"],
                "detail": f"'{doc['title']}' has {doc['sentences']} sentences but no "
                "entity mentions — annotation never ran",
            }
        )
    return issues


def find_issues(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive actionable issues from a health snapshot (pure function).

    Args:
        snapshot: A snapshot as returned by :func:`collect`.

    Returns:
        Issue dicts with ``code``, ``severity`` (``error``/``warning``/``info``),
        ``detail``, and ``document_id`` when the issue is document-scoped.
    """
    issues: list[dict[str, Any]] = []
    for doc in snapshot["documents"]:
        issues.extend(_document_issues(doc))
    for row in snapshot["jobs"]:
        if row["status"] == "failed":
            issues.append(
                {
                    "code": "job-failed",
                    "severity": "error",
                    "detail": f"{row['count']} failed {row['kind']} job(s) in the queue",
                }
            )
    for job in snapshot["running_jobs"]:
        if job["heartbeat_age_s"] > STALE_HEARTBEAT_SECONDS:
            issues.append(
                {
                    "code": "job-stalled",
                    "severity": "warning",
                    "detail": f"{job['kind']} job {job['id']} (worker {job['locked_by']}) "
                    f"last heartbeat {job['heartbeat_age_s']}s ago",
                }
            )
    totals = snapshot["totals"]
    if totals["sentences"] > 0 and totals["embeddings"] == 0:
        issues.append(
            {
                "code": "embeddings-missing",
                "severity": "info",
                "detail": (
                    "corpus has sentences but no learned embeddings — semantic search "
                    "unavailable; run the semantic_embed job (embedDocumentSentences)"
                ),
            }
        )
    return issues


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
    lines = ["doc-ray corpus health", "====================="]
    totals = " ".join(f"{k}={v:,}" for k, v in snapshot["totals"].items())
    lines += [f"totals: {totals}", ""]

    lines.append(f"documents ({len(snapshot['documents'])}):")
    for d in snapshot["documents"]:
        lang = d["language"] or "-"
        lines.append(
            f"  {str(d['title'])[:48]:48} lang={lang:4} sents={d['sentences']:>6,} "
            f"mentions={d['mentions']:>6,} links={d['links']:>4,}"
        )

    lines += ["", "jobs:"]
    lines += [f"  {r['kind']}/{r['status']}={r['count']}" for r in snapshot["jobs"]]
    for job in snapshot["running_jobs"]:
        lines.append(
            f"  running: {job['kind']} {job['id']} worker={job['locked_by']} "
            f"heartbeat_age={job['heartbeat_age_s']}s"
        )

    lines.append("")
    if not issues:
        lines.append("No issues found.")
    else:
        lines.append(f"issues ({len(issues)}):")
        lines += [f"  [{i['severity']}] {i['code']}: {i['detail']}" for i in issues]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the corpus health report CLI.

    Args:
        argv: Argument vector; defaults to ``sys.argv`` when ``None``.

    Returns:
        Process exit code: ``0`` healthy, ``2`` issues found, ``1`` when the
        database is unreachable.
    """
    ap = argparse.ArgumentParser(description="Report the health of the doc-ray corpus.")
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
