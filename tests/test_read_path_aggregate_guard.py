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
