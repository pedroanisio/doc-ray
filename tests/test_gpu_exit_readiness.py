"""Read-only GPU-exit readiness checks."""

from __future__ import annotations

import check_gpu_exit_readiness as readiness


class _Rows:
    def __init__(self, count: int) -> None:
        self.count = count
        self.executed = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str):
        self.executed = sql
        return self

    def fetchone(self) -> tuple[int]:
        return (self.count,)


def test_pending_tags_block_gpu_exit() -> None:
    conn = _Rows(46)
    result = readiness.check_readiness(
        {"DATABASE_URL": "postgresql://example"}, connect=lambda *_a, **_kw: conn
    )
    assert result.status == "blocked"
    assert result.pending_documents == 46
    assert "pending-trf-h200" in conn.executed


def test_zero_pending_tags_make_gpu_exit_ready() -> None:
    result = readiness.check_readiness(
        {"DATABASE_URL": "postgresql://example"}, connect=lambda *_a, **_kw: _Rows(0)
    )
    assert result.status == "ready"
    assert result.pending_documents == 0
