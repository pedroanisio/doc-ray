"""Unit tests for db_analyzer issue-derivation rules (pure functions)."""

from __future__ import annotations

from doc_ray.application.db_analyzer import quality as q
from doc_ray.application.db_analyzer.models import (
    ColumnInfo,
    ColumnProfile,
    ForeignKeyInfo,
    IndexInfo,
    TableProfile,
    TableStructure,
)


def _table(**over):
    base = dict(
        schema="public",
        name="orders",
        columns=[ColumnInfo("id", "integer", False, None, 1)],
        primary_key=["id"],
        foreign_keys=[],
        indexes=[IndexInfo("orders_pkey", ["id"], True, True, "", 5)],
        constraints=[],
        size_bytes=1024,
        seq_scan=0,
        idx_scan=5,
    )
    base.update(over)
    return TableStructure(**base)


def _col(**over):
    base = dict(
        name="c",
        data_type="text",
        row_count=10,
        null_count=0,
        null_pct=0.0,
        distinct_count=5,
        distinct_estimated=False,
    )
    base.update(over)
    return ColumnProfile(**base)


def _profile(**over):
    base = dict(
        schema="public", name="orders", row_count=10, columns=[_col()], duplicate_group_count=0
    )
    base.update(over)
    return TableProfile(**base)


def _codes(issues):
    return [i.code for i in issues]


def test_healthy_table_has_no_structural_issues():
    assert q.find_issues([_table()], []) == []


def test_missing_primary_key_flags_warning():
    issues = q.find_issues([_table(primary_key=[])], [])
    hit = next(i for i in issues if i.code == "missing-primary-key")
    assert hit.severity == "warning"


def test_unindexed_foreign_key_flags_warning():
    fk = ForeignKeyInfo("orders_customer_fkey", ["customer_id"], "public", "customers", ["id"])
    issues = q.find_issues([_table(foreign_keys=[fk])], [])
    hit = next(i for i in issues if i.code == "unindexed-foreign-key")
    assert hit.severity == "warning"
    assert "customers" in hit.detail


def test_indexed_foreign_key_is_not_flagged():
    fk = ForeignKeyInfo("orders_customer_fkey", ["customer_id"], "public", "customers", ["id"])
    idx = IndexInfo("orders_customer_idx", ["customer_id"], False, False, "", 3)
    issues = q.find_issues([_table(foreign_keys=[fk], indexes=[idx])], [])
    assert "unindexed-foreign-key" not in _codes(issues)


def test_unused_secondary_index_flags_info():
    idx = IndexInfo("orders_extra_idx", ["label"], False, False, "", 0)
    t = _table(indexes=[IndexInfo("orders_pkey", ["id"], True, True, "", 5), idx])
    issues = q.find_issues([t], [])
    hit = next(i for i in issues if i.code == "unused-index")
    assert hit.severity == "info"


def test_unused_primary_key_index_is_not_flagged():
    t = _table(indexes=[IndexInfo("orders_pkey", ["id"], True, True, "", 0)])
    assert "unused-index" not in _codes(q.find_issues([t], []))


def test_sequential_scan_heavy_large_table_flags_warning():
    t = _table(seq_scan=1000, idx_scan=0, size_bytes=q.LARGE_TABLE_BYTES + 1)
    issues = q.find_issues([t], [])
    hit = next(i for i in issues if i.code == "sequential-scan-heavy")
    assert hit.severity == "warning"


def test_sequential_scan_on_small_table_is_not_flagged():
    t = _table(seq_scan=1000, idx_scan=0, size_bytes=100)
    assert "sequential-scan-heavy" not in _codes(q.find_issues([t], []))


def test_column_always_null_flags_warning():
    p = _profile(columns=[_col(null_count=10, null_pct=100.0, distinct_count=None)])
    issues = q.find_issues([], [p])
    hit = next(i for i in issues if i.code == "column-always-null")
    assert hit.severity == "warning"


def test_high_null_rate_flags_warning_but_not_always_null():
    p = _profile(columns=[_col(row_count=10, null_count=6, null_pct=60.0)])
    issues = q.find_issues([], [p])
    codes = _codes(issues)
    assert "high-null-rate" in codes
    assert "column-always-null" not in codes


def test_constant_column_flags_info():
    p = _profile(columns=[_col(distinct_count=1)])
    issues = q.find_issues([], [p])
    hit = next(i for i in issues if i.code == "constant-column")
    assert hit.severity == "info"


def test_outliers_flag_info():
    p = _profile(columns=[_col(outlier_count=3)])
    issues = q.find_issues([], [p])
    hit = next(i for i in issues if i.code == "numeric-outliers")
    assert hit.severity == "info"
    assert "3" in hit.detail


def test_duplicate_rows_flag_warning():
    p = _profile(duplicate_group_count=2)
    issues = q.find_issues([], [p])
    hit = next(i for i in issues if i.code == "duplicate-rows")
    assert hit.severity == "warning"


def test_no_duplicate_groups_is_not_flagged():
    assert "duplicate-rows" not in _codes(q.find_issues([], [_profile(duplicate_group_count=0)]))


def test_exit_code_ignores_info_but_not_warnings():
    assert q.exit_code([]) == 0
    warn = q.find_issues([_table(primary_key=[])], [])
    assert q.exit_code(warn) == 2


def test_recommendations_are_deduped_and_skip_info():
    t = _table(primary_key=[])
    p = _profile(columns=[_col(distinct_count=1)])  # info-level constant-column
    issues = q.find_issues([t], [p])
    recs = q.recommendations(issues)
    assert len(recs) == 1
    assert "public.orders" in recs[0]
