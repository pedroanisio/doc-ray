"""CLI contract for the corpus-wide semantic embedding backfill."""

from __future__ import annotations

from typing import Any

import enqueue_semantic_backfill as cli
import pytest


class _Conn:
    """Async psycopg-shaped double recording the statements it is given."""

    def __init__(self, visible: int) -> None:
        self.visible = visible
        self.statements: list[str] = []

    async def execute(self, sql: str, params: Any = None) -> _Conn:
        self.statements.append(sql)
        return self

    async def fetchone(self) -> tuple[int]:
        return (self.visible,)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_backfill_assumes_the_bypassrls_batch_role() -> None:
    """Under forced RLS the app role sees no documents, so the CLI must SET ROLE."""
    conn = _Conn(580)
    await cli._prepare_session(conn)
    assert conn.statements[0] == f"SET ROLE {cli.BATCH_ROLE}"


@pytest.mark.asyncio
async def test_invisible_corpus_raises_instead_of_enqueueing_nothing() -> None:
    """Zero visible documents must fail loudly, not report a successful no-op."""
    with pytest.raises(cli.CorpusInvisible):
        await cli._prepare_session(_Conn(0))


def test_missing_database_url_exits_two(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert cli.main([]) == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


def test_default_enqueues_only_missing_and_reports_count(monkeypatch, capsys) -> None:
    calls: list[tuple[str, bool, int]] = []

    async def fake_run(database_url: str, *, only_missing: bool, priority: int) -> list[str]:
        calls.append((database_url, only_missing, priority))
        return ["job-a", "job-b"]

    monkeypatch.setenv("DATABASE_URL", "postgresql://example/test")
    monkeypatch.setattr(cli, "_run", fake_run)

    assert cli.main([]) == 0
    assert calls == [("postgresql://example/test", True, 100)]
    assert "semantic_embed: enqueued 2 job(s)" in capsys.readouterr().out


def test_all_requeues_and_only_missing_flag_is_explicit(monkeypatch) -> None:
    calls: list[bool] = []

    async def fake_run(database_url: str, *, only_missing: bool, priority: int) -> list[str]:
        calls.append(only_missing)
        return []

    monkeypatch.setenv("DATABASE_URL", "postgresql://example/test")
    monkeypatch.setattr(cli, "_run", fake_run)

    assert cli.main(["--all"]) == 0
    assert cli.main(["--only-missing"]) == 0
    assert calls == [False, True]
