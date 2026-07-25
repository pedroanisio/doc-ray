"""CLI contract for the corpus-wide semantic embedding backfill."""

from __future__ import annotations

import enqueue_semantic_backfill as cli


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
