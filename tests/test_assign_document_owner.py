"""Tests for scripts/assign_document_owner.py.

The script's whole job is deciding *which* rows move and *whether* to write, so
the connection is a recording fake: the suite runs offline and asserts the SQL
contract (email→principal resolution, selector, dry-run) rather than a live
UPDATE, which the DB-backed RLS suite already covers.
"""

from __future__ import annotations

import assign_document_owner as ado
import pytest


class FakeCursor:
    def __init__(self, rows: list[dict], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict]:
        return self._rows


class FakeConn:
    """Records every statement and replays canned rows in order."""

    def __init__(self, responses: list[FakeCursor]) -> None:
        self._responses = list(responses)
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, query: str, params: tuple = ()) -> FakeCursor:
        self.executed.append((" ".join(query.split()), params))
        return self._responses.pop(0) if self._responses else FakeCursor([])


def test_resolve_principal_matches_the_login_email_not_the_display_name() -> None:
    conn = FakeConn([FakeCursor([{"principal_id": "p-1"}])])
    assert ado.resolve_principal(conn, "  Someone@Example.COM ") == "p-1"
    query, params = conn.executed[0]
    assert "principal_credentials" in query, "display_name is a label, not an identity"
    assert params == ("someone@example.com",), "the email must be normalized before matching"


def test_resolve_principal_returns_none_for_an_unknown_email() -> None:
    conn = FakeConn([FakeCursor([])])
    assert ado.resolve_principal(conn, "nobody@example.com") is None


def test_select_documents_unowned_targets_null_ownership() -> None:
    conn = FakeConn([FakeCursor([{"id": "d-1"}, {"id": "d-2"}])])
    assert ado.select_documents(conn, unowned=True, from_principal=None) == ["d-1", "d-2"]
    query, _params = conn.executed[0]
    assert "owner_principal_id IS NULL" in query


def test_select_documents_from_principal_targets_that_owner() -> None:
    conn = FakeConn([FakeCursor([{"id": "d-9"}])])
    assert ado.select_documents(conn, unowned=False, from_principal="p-7") == ["d-9"]
    query, params = conn.executed[0]
    assert "owner_principal_id = %s" in query
    assert params == ("p-7",)


def test_assign_is_a_no_op_for_an_empty_selection() -> None:
    conn = FakeConn([])
    assert ado.assign(conn, [], "p-1") == 0
    assert conn.executed == [], "an empty move must not issue an UPDATE"


def test_assign_updates_the_selected_documents() -> None:
    conn = FakeConn([FakeCursor([], rowcount=2)])
    assert ado.assign(conn, ["d-1", "d-2"], "p-1") == 2
    query, params = conn.executed[0]
    assert "UPDATE documents SET owner_principal_id" in query
    assert params == ("p-1", ["d-1", "d-2"])


def test_main_dry_run_does_not_write(monkeypatch, capsys) -> None:
    conn = FakeConn([FakeCursor([{"principal_id": "p-1"}]), FakeCursor([{"id": "d-1"}])])
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setattr(ado.psycopg, "connect", lambda *a, **k: _ctx(conn))
    assert ado.main(["--email", "a@example.com", "--unowned"]) == 0
    assert "dry run" in capsys.readouterr().out
    assert not any("UPDATE" in query for query, _ in conn.executed)


def test_main_apply_commits_the_move(monkeypatch, capsys) -> None:
    conn = FakeConn(
        [
            FakeCursor([{"principal_id": "p-1"}]),
            FakeCursor([{"id": "d-1"}]),
            FakeCursor([], rowcount=1),
        ]
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setattr(ado.psycopg, "connect", lambda *a, **k: _ctx(conn))
    assert ado.main(["--email", "a@example.com", "--unowned", "--apply"]) == 0
    assert "assigned 1 document" in capsys.readouterr().out
    assert any("UPDATE" in query for query, _ in conn.executed)


def test_main_refuses_an_unknown_recipient(monkeypatch, capsys) -> None:
    conn = FakeConn([FakeCursor([])])
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setattr(ado.psycopg, "connect", lambda *a, **k: _ctx(conn))
    assert ado.main(["--email", "ghost@example.com", "--unowned", "--apply"]) == 1
    assert "no principal" in capsys.readouterr().err


def test_main_requires_a_database_url(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert ado.main(["--email", "a@example.com", "--unowned"]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_selectors_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        ado.main(["--email", "a@example.com", "--unowned", "--from-email", "b@example.com"])


class _ctx:
    """Minimal context manager standing in for a psycopg connection."""

    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    def __enter__(self) -> FakeConn:
        return self._conn

    def __exit__(self, *exc: object) -> bool:
        return False
