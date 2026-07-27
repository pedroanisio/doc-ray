"""The D-024 gate: read paths must not aggregate large fact tables live.

Guards the guard (scripts/check_read_path_aggregates.py): it must stay green on
the tree, catch a planted fact-table aggregate, and not fire on scans without an
aggregate. See D-025 in engineering_decisions.yml.
"""

from __future__ import annotations

import check_read_path_aggregates as guard


def test_guard_is_green_on_the_repository():
    violations = guard.find_violations()
    assert violations == [], f"unexpected read-path fact-table aggregates: {violations}"


def test_guard_detects_a_planted_fact_table_aggregate(tmp_path):
    (tmp_path / "bad_read.py").write_text(
        'def r(q):\n    return q("SELECT count(*) FROM lexeme_occurrences GROUP BY lexeme_uid")\n',
        encoding="utf-8",
    )
    tables = {t for _, _, t in guard.find_violations((str(tmp_path),))}
    assert "lexeme_occurrences" in tables


def test_guard_ignores_a_scan_without_an_aggregate(tmp_path):
    (tmp_path / "ok_read.py").write_text(
        'def r(q):\n    return q("SELECT id FROM spans WHERE document_id = %s LIMIT 10")\n',
        encoding="utf-8",
    )
    assert guard.find_violations((str(tmp_path),)) == []


def test_guard_ignores_prose_docstrings_mentioning_a_table(tmp_path):
    (tmp_path / "prose.py").write_text(
        '"""We count sentences per document (a distinct group by section)."""\n',
        encoding="utf-8",
    )
    assert guard.find_violations((str(tmp_path),)) == []


def test_guard_accepts_a_count_over_a_limit_bounded_derived_table(tmp_path):
    """D-024 forbids *unbounded* live aggregation; a LIMIT makes the work bounded.

    The term-frequency sentence scan (#39) counts matching sentences to decide a
    flood-risk band. It can stop at the cap, because past the cap the band is
    already decided — so the inner scan carries a LIMIT and the count is O(cap),
    not O(corpus). That is the shape D-024 exists to permit, and recognising it
    is what keeps the rule an invariant instead of a list of exceptions.
    """
    (tmp_path / "bounded_read.py").write_text(
        'def r(q):\n    return q("SELECT count(*) AS hits '
        'FROM (SELECT 1 FROM sentences s WHERE s.text ILIKE %(p)s LIMIT %(cap)s) b")\n',
        encoding="utf-8",
    )
    assert guard.find_violations((str(tmp_path),)) == []


def test_guard_still_catches_an_unbounded_count_over_the_same_table(tmp_path):
    """The LIMIT is load-bearing: drop it and the same query is a violation."""
    (tmp_path / "unbounded_read.py").write_text(
        'def r(q):\n    return q("SELECT count(*) AS hits '
        'FROM (SELECT 1 FROM sentences s WHERE s.text ILIKE %(p)s) b")\n',
        encoding="utf-8",
    )
    tables = {t for _, _, t in guard.find_violations((str(tmp_path),))}
    assert "sentences" in tables


def test_guard_catches_an_aggregate_outside_the_bounded_subquery(tmp_path):
    """A LIMIT somewhere in the statement does not license an unbounded aggregate.

    Here the bounded derived table is real, but a second, unbounded reference to
    the fact table is aggregated alongside it — exactly the regression a naive
    "contains LIMIT" check would wave through.
    """
    (tmp_path / "mixed_read.py").write_text(
        'def r(q):\n    return q("SELECT (SELECT count(*) FROM sentences) AS total, count(*) '
        'FROM (SELECT 1 FROM sentences s WHERE s.text ILIKE %(p)s LIMIT %(cap)s) b")\n',
        encoding="utf-8",
    )
    tables = {t for _, _, t in guard.find_violations((str(tmp_path),))}
    assert "sentences" in tables
