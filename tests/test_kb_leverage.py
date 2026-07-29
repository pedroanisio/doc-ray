"""Data-driven external-KB leverage analysis (read-only)."""

from __future__ import annotations

from typing import Any

import analyze_kb_leverage as leverage

_CORPUS = (638, 20, 658)
_COMPUTE = [
    ("annotate", 683, 23.8),
    ("visual_caption", 149, 23.5),
    ("visual_embed", 319, 10.5),
    ("semantic_embed", 63, 0.9),
]


class _Conn:
    """Minimal psycopg-shaped double dispatching on SQL fragments."""

    def __init__(self, corpus: tuple[int, int, int], compute: list[tuple[Any, ...]]) -> None:
        self.corpus = corpus
        self.compute = compute
        self.statements: list[str] = []

    def __enter__(self) -> _Conn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _Conn:
        self.statements.append(sql)
        self._last = sql
        return self

    def fetchone(self) -> tuple[Any, ...]:
        if "FROM documents" in self._last:
            return self.corpus
        if "FROM sentences" in self._last:
            return (2_644_343,)
        return (929_265,)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.compute


def _analyze(conn: _Conn, **kwargs: Any) -> leverage.LeverageReport:
    return leverage.analyze(
        {"DATABASE_URL": "postgresql://example"}, connect=lambda *_a, **_kw: conn, **kwargs
    )


def test_missing_database_url_is_an_error() -> None:
    assert leverage.analyze({}).status == "error"


def test_batch_role_is_assumed_before_counting() -> None:
    conn = _Conn(_CORPUS, _COMPUTE)
    _analyze(conn)
    assert conn.statements[0] == f"SET ROLE {leverage.BATCH_ROLE}"


def test_invisible_corpus_fails_closed_instead_of_reporting_leverage() -> None:
    """An RLS-filtered corpus reads as zero rows; that must not pass as a verdict."""
    report = _analyze(_Conn((0, 0, 0), _COMPUTE))
    assert report.status == "error"
    assert "RLS" in report.detail
    assert report.compute is None


def test_retrieval_displaceable_share_is_measured_not_assumed() -> None:
    report = _analyze(_Conn(_CORPUS, _COMPUTE))
    assert report.compute is not None
    # Only semantic_embed (0.9h) is displaceable by a retrieval index.
    assert report.compute.displaceable_hours == 0.9
    assert report.compute.already_remote_hours == 23.5
    assert report.compute.not_displaceable_hours == 34.3
    assert report.compute.displaceable_share == round(0.9 / 58.7, 4)
    assert report.status == "leverage-marginal"


def test_threshold_governs_the_verdict() -> None:
    report = _analyze(_Conn(_CORPUS, _COMPUTE), material_threshold=0.001)
    assert report.status == "leverage-material"


def test_unknown_job_kinds_default_to_not_displaceable() -> None:
    report = _analyze(_Conn(_CORPUS, [("brand_new_kind", 1, 5.0)]))
    assert report.compute is not None
    assert report.compute.displaceable_hours == 0.0
    assert report.compute.by_kind[0].classification == "not_displaceable"


def test_coverage_reports_both_denominators() -> None:
    report = _analyze(_Conn(_CORPUS, _COMPUTE), kb_submitted=562, kb_indexed=553)
    cov = report.coverage
    assert cov is not None
    # What a KB console shows...
    assert cov.share_of_submitted == round(553 / 562, 4)
    # ...versus real corpus coverage, which is materially lower.
    assert cov.share_of_extractable == round(553 / 638, 4)
    assert cov.share_of_extractable < cov.share_of_submitted
    assert cov.never_submitted == 76


def test_coverage_is_absent_rather_than_guessed_when_kb_stats_missing() -> None:
    report = _analyze(_Conn(_CORPUS, _COMPUTE))
    assert report.coverage is not None
    assert report.coverage.kb_indexed is None
    assert report.coverage.share_of_extractable is None


def test_local_semantic_index_coverage_is_reported() -> None:
    report = _analyze(_Conn(_CORPUS, _COMPUTE))
    assert report.semantic is not None
    assert report.semantic.embedded_share == round(929_265 / 2_644_343, 4)


def test_driver_failure_is_an_error_not_a_verdict() -> None:
    def _boom(*_a: object, **_kw: object) -> Any:
        raise RuntimeError("connection refused")

    report = leverage.analyze({"DATABASE_URL": "postgresql://example"}, connect=_boom)
    assert report.status == "error"
    assert "connection refused" in report.detail
