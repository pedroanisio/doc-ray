"""Safety guards for DB-backed integration tests."""

from __future__ import annotations

import _itdb
import pytest


class _FakeConn:
    def __init__(self, *, non_fixture_count: int = 0) -> None:
        self.non_fixture_count = non_fixture_count
        self.queries: list[str] = []

    def execute(self, query: str, params=()):
        self.queries.append(" ".join(query.split()))
        return self

    def fetchone(self):
        query = self.queries[-1]
        if "to_regclass('public.documents')" in query:
            return {"documents_table": "documents"}
        if "count(*) AS n" in query:
            return {"n": self.non_fixture_count}
        raise AssertionError(f"unexpected query: {query}")


def test_live_database_guard_rejects_non_fixture_documents(monkeypatch):
    monkeypatch.delenv("DOCRAY_TEST_ALLOW_LIVE_DB", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    conn = _FakeConn(non_fixture_count=2)

    with pytest.raises(_itdb.UnsafeTestDatabaseError, match="non-fixture documents"):
        _itdb.require_safe_test_database(
            conn,
            db_url="postgresql://docray:docray@localhost:5455/docray",
        )


def test_live_database_guard_rejects_empty_non_test_database_locally(monkeypatch):
    monkeypatch.delenv("DOCRAY_TEST_ALLOW_LIVE_DB", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    conn = _FakeConn(non_fixture_count=0)

    with pytest.raises(_itdb.UnsafeTestDatabaseError, match="non-test database"):
        _itdb.require_safe_test_database(
            conn,
            db_url="postgresql://docray:docray@localhost:5455/docray",
        )


def test_live_database_guard_preserves_github_actions_ephemeral_db(monkeypatch):
    monkeypatch.delenv("DOCRAY_TEST_ALLOW_LIVE_DB", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    conn = _FakeConn(non_fixture_count=0)

    _itdb.require_safe_test_database(
        conn,
        db_url="postgresql://docray:docray@localhost:5432/docray",
    )


def test_live_database_guard_allows_explicit_operator_override(monkeypatch):
    monkeypatch.setenv("DOCRAY_TEST_ALLOW_LIVE_DB", "1")
    conn = _FakeConn(non_fixture_count=2)

    _itdb.require_safe_test_database(
        conn,
        db_url="postgresql://docray:docray@localhost:5455/docray",
    )


def test_live_database_guard_allows_dedicated_test_database(monkeypatch):
    monkeypatch.delenv("DOCRAY_TEST_ALLOW_LIVE_DB", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    conn = _FakeConn(non_fixture_count=2)

    _itdb.require_safe_test_database(
        conn,
        db_url="postgresql://docray_test:secret@test-postgres:5432/docray_test",
    )

    assert conn.queries == []
