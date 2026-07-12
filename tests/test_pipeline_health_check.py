"""Tests for scripts/pipeline_health_check.py.

q/q1 are stubbed with a dispatcher keyed off the SQL text (same style as
tests/test_webapp.py's operator-health fakes), so these run without Postgres.
"""

from __future__ import annotations

import json

import pipeline_health_check as phc
import psycopg

DOC_ID = "11111111-1111-1111-1111-111111111111"


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _rows(sql: str) -> list[dict]:
    """One dispatcher shared by q/q1 and the fake connection: every branch
    returns a list of rows; q1 callers take the first (or None)."""
    s = _norm(sql)
    if "FROM tokens" in s and "entity_mention" in s and "canonical" in s:
        return [
            {
                "documents": 2,
                "sentences": 120,
                "tokens": 0,
                "entity_mentions": 0,
                "entity_links": 0,
                "canonical_entries": 0,
                "lexemes": 0,
                "embeddings": 0,
            }
        ]
    if "FROM documents d" in s and "ORDER BY d.created_at" in s:
        return [
            {
                "id": DOC_ID,
                "title": "stuck-doc",
                "language": None,
                "status": "extracted",
                "sentences": 120,
                "mentions": 0,
                "links": 0,
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "title": "empty-doc",
                "language": "en",
                "status": "extracted",
                "sentences": 0,
                "mentions": 0,
                "links": 0,
            },
        ]
    if "FROM jobs GROUP BY kind, status" in s:
        return [{"kind": "annotate", "status": "failed", "count": 2}]
    if "FROM jobs WHERE status = 'running'" in s:
        return [
            {
                "id": "job-stalled",
                "kind": "canonicalize",
                "locked_by": "worker-1",
                "heartbeat_age_s": 999,
            }
        ]
    if "FROM jobs j LEFT JOIN documents d" in s:
        return [
            {
                "id": "job-on-last-attempt",
                "kind": "annotate",
                "status": "running",
                "attempts": 3,
                "max_attempts": 3,
                "locked_by": None,
                "document_id": DOC_ID,
                "document_title": "stuck-doc",
                "progress_current": 3,
                "progress_total": 7,
                "progress_stage": "Registering token and entity spans",
                "runtime_s": 4389,
                "heartbeat_age_s": None,
                "queued_age_s": 9000,
                "reclaim_after_s": None,
                "error": "reclaimed: stale heartbeat, attempts exhausted",
            },
            {
                "id": "job-queued-too-long",
                "kind": "ingest",
                "status": "queued",
                "attempts": 0,
                "max_attempts": 3,
                "locked_by": None,
                "document_id": None,
                "document_title": None,
                "progress_current": None,
                "progress_total": None,
                "progress_stage": None,
                "runtime_s": None,
                "heartbeat_age_s": None,
                "queued_age_s": 600,
                "reclaim_after_s": None,
                "error": "",
            },
            {
                "id": "job-queued-fresh",
                "kind": "backup",
                "status": "queued",
                "attempts": 0,
                "max_attempts": 3,
                "locked_by": None,
                "document_id": None,
                "document_title": None,
                "progress_current": None,
                "progress_total": None,
                "progress_stage": None,
                "runtime_s": None,
                "heartbeat_age_s": None,
                "queued_age_s": 5,
                "reclaim_after_s": None,
                "error": "",
            },
        ]
    if "pg_database_size(current_database()) AS bytes" in s:
        return [{"bytes": 1024, "pretty": "1024 bytes"}]
    if "FROM pg_settings" in s:
        return [{"max_connections": 100}]
    if "FROM pg_stat_activity" in s:
        return []
    if "FROM pg_stat_user_tables" in s:
        return []
    raise AssertionError(f"unexpected query: {s}")


def _clean_rows(sql: str) -> list[dict]:
    s = _norm(sql)
    if "FROM tokens" in s and "entity_mention" in s and "canonical" in s:
        return [
            {
                "documents": 0,
                "sentences": 0,
                "tokens": 0,
                "entity_mentions": 0,
                "entity_links": 0,
                "canonical_entries": 0,
                "lexemes": 0,
                "embeddings": 0,
            }
        ]
    if "pg_database_size(current_database()) AS bytes" in s:
        return [{"bytes": 1024, "pretty": "1024 bytes"}]
    if "FROM pg_settings" in s:
        return [{"max_connections": 100}]
    if s.startswith("SELECT") and "FROM" in s:
        return []
    raise AssertionError(f"unexpected query: {s}")


def _q(sql: str) -> list[dict]:
    return _rows(sql)


def _q1(sql: str) -> dict | None:
    rows = _rows(sql)
    return rows[0] if rows else None


def test_collect_surfaces_every_known_broken_state():
    report = phc.collect(_q, _q1)
    codes = {issue["code"] for issue in report["corpus"]["issues"]}

    assert "document-empty" in codes
    assert "fts-degraded" in codes
    assert "document-unannotated" in codes
    assert "job-failed" in codes
    assert "job-stalled" in codes
    assert "job-last-attempt" in codes
    assert "job-reclaimed" in codes
    assert "queue-backlog" in codes


def test_queue_backlog_ignores_freshly_queued_jobs():
    report = phc.collect(_q, _q1)
    details = [
        issue["detail"] for issue in report["corpus"]["issues"] if issue["code"] == "queue-backlog"
    ]
    assert any("job-queued-too-long" in detail for detail in details)
    assert not any("job-queued-fresh" in detail for detail in details)


def test_collect_reports_nothing_for_a_healthy_corpus():
    report = phc.collect(_clean_rows, lambda sql: (_clean_rows(sql) or [None])[0])
    assert phc._all_issues(report) == []


def test_main_exit_code_reflects_worst_severity(monkeypatch, capsys):
    monkeypatch.setattr(phc, "_connect", lambda dsn: _FakeConn(_rows))
    assert phc.main([]) == 2  # document-empty/job-failed are error severity

    monkeypatch.setattr(phc, "_connect", lambda dsn: _FakeConn(_clean_rows))
    assert phc.main([]) == 0
    assert "No broken pipeline state found." in capsys.readouterr().out


def test_main_gives_actionable_hint_when_database_url_is_unset(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def raise_auth_failure(dsn):
        raise psycopg.OperationalError("FATAL: password authentication failed for user 'docray'")

    monkeypatch.setattr(phc, "_connect", raise_auth_failure)

    assert phc.main([]) == 3
    err = capsys.readouterr().err
    assert "source .env" in err
    assert "DATABASE_URL" in err


def test_main_json_output_is_parseable(monkeypatch, capsys):
    monkeypatch.setattr(phc, "_connect", lambda dsn: _FakeConn(_rows))
    phc.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert any(i["code"] == "queue-backlog" for i in payload["corpus"]["issues"])


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """Stands in for psycopg.Connection: routes .execute(sql) through a rows fake."""

    def __init__(self, rows_fn):
        self._rows_fn = rows_fn

    def execute(self, sql):
        return _FakeCursor(self._rows_fn(sql))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
