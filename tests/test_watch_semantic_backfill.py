"""Backfill watch: progress rendering and the drained-vs-invisible distinction."""

from __future__ import annotations

from typing import Any

import pytest
import watch_semantic_backfill as watch

_DRAINING = {
    "queued": 518,
    "running": 1,
    "succeeded": 124,
    "failed": 51,
    "documents": 637,
    "embedded": 124,
    "coverage": 124 / 637,
    "avg_seconds": 109.1,
    "eta_hours": 15.7,
    "draining": True,
}


class _Conn:
    """psycopg-shaped double answering each query by SQL fragment."""

    def __init__(self, visible: int = 637) -> None:
        self.visible = visible
        self.statements: list[str] = []

    def execute(self, sql: str, params: Any = None) -> _Conn:
        self.statements.append(sql)
        self._last = sql
        return self

    def fetchone(self) -> tuple[Any, ...]:
        if "WHERE deleted_at IS NULL" in self._last and "count(*)" in self._last.split("FROM")[0]:
            return (self.visible,)
        if "kind = 'semantic_embed'" in self._last and "FILTER" in self._last:
            return (518, 1, 124, 51)
        if "avg(extract" in self._last:
            return (109.1,)
        return (637, 124)

    def close(self) -> None:
        return None


def test_sample_computes_coverage_and_eta_from_measured_rate() -> None:
    s = watch.sample(_Conn())
    assert s["queued"] == 518
    assert s["embedded"] == 124
    assert s["coverage"] == pytest.approx(124 / 637)
    # 518 queued at 109.1s each -> ~15.7h, not a guess.
    assert s["eta_hours"] == pytest.approx(15.7, abs=0.1)
    assert s["draining"] is True


def test_a_drained_queue_is_reported_as_drained() -> None:
    s = dict(_DRAINING, queued=0, running=0, draining=False)
    assert "DRAINED" in watch.render(s)


def test_progress_line_carries_queue_coverage_and_eta() -> None:
    line = watch.render(_DRAINING)
    assert "queued=518" in line
    assert "124/637" in line
    assert "eta=15.7h" in line
    assert "DRAINED" not in line


def test_invisible_corpus_is_not_logged_as_a_finished_backfill(monkeypatch) -> None:
    """RLS hides every row from the app role; zero must not read as 'drained'."""
    monkeypatch.setattr(watch.psycopg, "connect", lambda *_a, **_kw: _Conn(visible=0))

    with pytest.raises(RuntimeError, match="refusing to log an unreadable corpus"):
        watch._connect("postgresql://example")


def test_visible_corpus_connects_cleanly(monkeypatch) -> None:
    monkeypatch.setattr(watch.psycopg, "connect", lambda *_a, **_kw: _Conn(visible=637))

    conn = watch._connect("postgresql://example")
    assert conn.statements[0] == f"SET ROLE {watch.BATCH_ROLE}"
