"""Tests for the Postgres instance health report (unit: pure issue rules; integration: live DB)."""

from __future__ import annotations

import json

from doc_ray.application import db_health as dh

# --- snapshot builders -------------------------------------------------------


def _snapshot(**over):
    snap = {
        "size_bytes": 1024,
        "size_pretty": "1024 bytes",
        "max_connections": 100,
        "connections": [],
        "tables": [{"relname": "documents", "n_live_tup": 10, "n_dead_tup": 0, "dead_pct": 0.0}],
    }
    snap.update(over)
    return snap


def _codes(issues):
    return [i["code"] for i in issues]


# --- unit: issue rules --------------------------------------------------------


def test_healthy_snapshot_has_no_issues():
    assert dh.find_issues(_snapshot()) == []


def test_connection_pressure_flags_warning():
    snap = _snapshot(
        max_connections=10,
        connections=[
            {"pid": i, "state": "idle", "wait_event_type": None, "duration_s": 1, "query": ""}
            for i in range(9)
        ],
    )
    issues = dh.find_issues(snap)
    hit = next(i for i in issues if i["code"] == "connection-pressure")
    assert hit["severity"] == "warning"
    assert "10/10" in hit["detail"]


def test_low_connection_usage_is_not_flagged():
    snap = _snapshot(
        max_connections=100,
        connections=[
            {"pid": 1, "state": "idle", "wait_event_type": None, "duration_s": 1, "query": ""}
        ],
    )
    assert "connection-pressure" not in _codes(dh.find_issues(snap))


def test_slow_active_query_flags_warning():
    snap = _snapshot(
        connections=[
            {
                "pid": 42,
                "state": "active",
                "wait_event_type": None,
                "duration_s": dh.LONG_QUERY_SECONDS + 1,
                "query": "select * from big_table",
            }
        ]
    )
    issues = dh.find_issues(snap)
    hit = next(i for i in issues if i["code"] == "query-slow")
    assert hit["severity"] == "warning"
    assert "42" in hit["detail"]


def test_fast_active_query_is_not_flagged():
    snap = _snapshot(
        connections=[
            {
                "pid": 42,
                "state": "active",
                "wait_event_type": None,
                "duration_s": 1,
                "query": "select 1",
            }
        ]
    )
    assert "query-slow" not in _codes(dh.find_issues(snap))


def test_idle_in_transaction_flags_warning():
    snap = _snapshot(
        connections=[
            {
                "pid": 7,
                "state": "idle in transaction",
                "wait_event_type": None,
                "duration_s": dh.IDLE_IN_TRANSACTION_SECONDS + 1,
                "query": "select 1",
            }
        ]
    )
    issues = dh.find_issues(snap)
    hit = next(i for i in issues if i["code"] == "idle-in-transaction")
    assert hit["severity"] == "warning"
    assert "7" in hit["detail"]


def test_lock_wait_flags_error():
    snap = _snapshot(
        connections=[
            {
                "pid": 9,
                "state": "active",
                "wait_event_type": "Lock",
                "duration_s": 1,
                "query": "update documents set title = 'x'",
            }
        ]
    )
    issues = dh.find_issues(snap)
    hit = next(i for i in issues if i["code"] == "query-blocked")
    assert hit["severity"] == "error"
    assert "9" in hit["detail"]


def test_table_bloat_flags_warning_above_threshold():
    snap = _snapshot(
        tables=[
            {
                "relname": "spans",
                "n_live_tup": 8000,
                "n_dead_tup": 2000,
                "dead_pct": 20.0 + 1,
            }
        ]
    )
    issues = dh.find_issues(snap)
    hit = next(i for i in issues if i["code"] == "table-bloat")
    assert hit["severity"] == "warning"
    assert "spans" in hit["detail"]


def test_small_table_bloat_is_not_flagged():
    snap = _snapshot(
        tables=[{"relname": "tiny", "n_live_tup": 2, "n_dead_tup": 8, "dead_pct": 80.0}]
    )
    assert "table-bloat" not in _codes(dh.find_issues(snap))


def test_exit_code_ignores_none_but_not_warnings():
    assert dh.exit_code([]) == 0
    assert dh.exit_code([{"code": "x", "severity": "warning", "detail": ""}]) == 2
    assert dh.exit_code([{"code": "x", "severity": "error", "detail": ""}]) == 2


# --- unit: rendering ----------------------------------------------------------


def test_render_text_shows_size_connections_and_issues():
    snap = _snapshot(
        connections=[
            {
                "pid": 42,
                "state": "active",
                "wait_event_type": None,
                "duration_s": dh.LONG_QUERY_SECONDS + 1,
                "query": "select 1",
            }
        ]
    )
    out = dh.render_text(snap, dh.find_issues(snap))
    assert "1024 bytes" in out
    assert "2/100" in out
    assert "query-slow" in out


def test_render_text_healthy_says_so():
    out = dh.render_text(_snapshot(), [])
    assert "no issues" in out.lower()


# --- integration: live DB -----------------------------------------------------


def test_collect_against_live_db(pg):
    conn, _created = pg
    snap = dh.collect(conn)
    assert snap["size_bytes"] > 0
    assert snap["max_connections"] > 0
    assert isinstance(snap["connections"], list)
    assert isinstance(snap["tables"], list)
    # find_issues must run without error against a real snapshot shape.
    dh.find_issues(snap)


def test_cli_json_output(pg, monkeypatch, capsys):
    import _itdb

    monkeypatch.setenv("DATABASE_URL", _itdb.DB_URL)
    rc = dh.main(["--json"])
    assert rc in (0, 2)
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "size_bytes",
        "size_pretty",
        "max_connections",
        "connections",
        "tables",
        "issues",
    }
