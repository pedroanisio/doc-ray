"""The webapp auth-session store persists across restarts (Postgres backend).

Regression for the "logged out whenever the webapp container restarts" report:
sessions used to live in a process-local dict (``InMemoryAuthSessionStore``), so a
restart — or a second replica — wiped every login. The store is now a swappable
proxy that keeps in-memory for tests/local use and a durable Postgres backend
(``web_auth_sessions`` table, migration 112) in production.

The Postgres backend is exercised here against a fake psycopg connection (no DB),
asserting the exact SQL it issues; a live round-trip is covered by the migration
+ integration suite when a database is available.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from webapp import auth_session
from webapp.auth_session import (
    InMemoryAuthSessionStore,
    WebAuthSession,
    auth_sessions,
    configure_auth_sessions,
)


@pytest.fixture(autouse=True)
def _restore_backend():
    """Every test starts and ends on a clean in-memory backend."""
    auth_sessions.use_backend(InMemoryAuthSessionStore())
    yield
    auth_sessions.use_backend(InMemoryAuthSessionStore())


# --- proxy + configuration --------------------------------------------------


def test_proxy_defaults_to_in_memory_and_round_trips():
    sid = auth_sessions.create(principal_id="p1", subject="local:a@example.com")
    record = auth_sessions.get(sid)
    assert record is not None
    assert record.principal_id == "p1"
    auth_sessions.revoke(sid)
    assert auth_sessions.get(sid) is None


def test_configure_memory_uses_in_memory_backend():
    configure_auth_sessions(store="memory", database_url="postgresql://ignored")
    assert isinstance(auth_sessions.backend, InMemoryAuthSessionStore)


def test_configure_postgres_uses_the_durable_backend():
    from webapp.auth_session_postgres import PostgresAuthSessionStore

    configure_auth_sessions(store="postgres", database_url="postgresql://x/db")
    assert isinstance(auth_sessions.backend, PostgresAuthSessionStore)


def test_unknown_store_kind_falls_back_to_in_memory():
    configure_auth_sessions(store="", database_url="postgresql://x/db")
    assert isinstance(auth_sessions.backend, InMemoryAuthSessionStore)


# --- Postgres backend SQL (fake connection, no DB) --------------------------


class _FakeCursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _FakeConn:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> _FakeCursor:
        self.calls.append((sql, params))
        return _FakeCursor(self._row)


def _patch_conn(monkeypatch, row):
    from webapp import auth_session_postgres

    conn = _FakeConn(row)
    monkeypatch.setattr(auth_session_postgres.psycopg, "connect", lambda *a, **k: conn)
    return conn


def test_postgres_create_inserts_and_returns_a_session_id(monkeypatch):
    from webapp.auth_session_postgres import PostgresAuthSessionStore

    conn = _patch_conn(monkeypatch, None)
    store = PostgresAuthSessionStore("postgresql://x/db")
    sid = store.create(
        principal_id="0260a042-9b61-41b0-86e7-3ce8f38aa2dc",
        subject="oidc:a@example.com",
        ttl_seconds=3600,
        bearer_token="id-token",
    )
    assert isinstance(sid, str) and sid
    sql, params = conn.calls[0]
    assert "INSERT INTO web_auth_sessions" in sql
    assert sid in params
    assert "0260a042-9b61-41b0-86e7-3ce8f38aa2dc" in params
    assert "id-token" in params


def test_postgres_get_returns_active_session(monkeypatch):
    from webapp.auth_session_postgres import PostgresAuthSessionStore

    now = datetime.now(UTC)
    row = {
        "session_id": "sid-1",
        "principal_id": "p-1",
        "subject": "local:a@example.com",
        "bearer_token": None,
        "issued_at": now,
        "expires_at": now + timedelta(hours=1),
    }
    conn = _patch_conn(monkeypatch, row)
    store = PostgresAuthSessionStore("postgresql://x/db")
    record = store.get("sid-1")
    assert isinstance(record, WebAuthSession)
    assert record.principal_id == "p-1"
    assert "SELECT" in conn.calls[0][0]


def test_postgres_get_revokes_and_returns_none_when_expired(monkeypatch):
    from webapp.auth_session_postgres import PostgresAuthSessionStore

    now = datetime.now(UTC)
    row = {
        "session_id": "sid-1",
        "principal_id": "p-1",
        "subject": "s",
        "bearer_token": None,
        "issued_at": now - timedelta(hours=2),
        "expires_at": now - timedelta(hours=1),  # already expired
    }
    conn = _patch_conn(monkeypatch, row)
    store = PostgresAuthSessionStore("postgresql://x/db")
    assert store.get("sid-1") is None
    # a DELETE must follow the SELECT to purge the expired row
    assert any("DELETE FROM web_auth_sessions" in sql for sql, _ in conn.calls)


def test_postgres_get_returns_none_for_unknown_id(monkeypatch):
    from webapp.auth_session_postgres import PostgresAuthSessionStore

    _patch_conn(monkeypatch, None)
    store = PostgresAuthSessionStore("postgresql://x/db")
    assert store.get("nope") is None
    assert store.get(None) is None


def test_postgres_revoke_deletes(monkeypatch):
    from webapp.auth_session_postgres import PostgresAuthSessionStore

    conn = _patch_conn(monkeypatch, None)
    store = PostgresAuthSessionStore("postgresql://x/db")
    store.revoke("sid-1")
    assert conn.calls[0][0].startswith("DELETE FROM web_auth_sessions")
    assert conn.calls[0][1] == ("sid-1",)


def test_migration_112_creates_the_table():
    """The table the durable store depends on ships as a migration."""
    from pathlib import Path

    sql = Path("db/migrations/112_web_auth_sessions.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE" in sql and "web_auth_sessions" in sql
    # additive migration must not rewrite the schema version (avoids drift gate)
    assert "schema_version" not in sql or "schema_migrations" in sql


def test_auth_session_module_exposes_configure():
    assert hasattr(auth_session, "configure_auth_sessions")
