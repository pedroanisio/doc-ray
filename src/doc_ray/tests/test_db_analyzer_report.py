"""Unit tests for db_analyzer report rendering (Markdown + JSON)."""

from __future__ import annotations

import json

from doc_ray.application.db_analyzer import report
from doc_ray.application.db_analyzer.models import (
    AnalysisResult,
    ColumnInfo,
    ColumnProfile,
    ForeignKeyInfo,
    Issue,
    TableProfile,
    TableStructure,
)


def _result():
    fk = ForeignKeyInfo("orders_customer_fkey", ["customer_id"], "public", "customers", ["id"])
    table = TableStructure(
        schema="public",
        name="orders",
        columns=[
            ColumnInfo("id", "integer", False, None, 1),
            ColumnInfo("customer_id", "integer", True, None, 2),
        ],
        primary_key=["id"],
        foreign_keys=[fk],
        indexes=[],
        constraints=[],
        size_bytes=2048,
        seq_scan=10,
        idx_scan=0,
    )
    profile = TableProfile(
        schema="public",
        name="orders",
        row_count=100,
        columns=[
            ColumnProfile("id", "integer", 100, 0, 0.0, distinct_count=100),
            ColumnProfile(
                "customer_id", "integer", 100, 5, 5.0, distinct_count=20, min_value=1, max_value=99
            ),
        ],
        duplicate_group_count=0,
    )
    issues = [
        Issue("missing-primary-key", "warning", "public.other", "public.other has no PK", "quality"),
        Issue(
            "unindexed-foreign-key",
            "warning",
            "public.orders(customer_id)",
            "fk not indexed",
            "performance",
        ),
    ]
    return AnalysisResult(
        engine="postgres",
        database="docray",
        schemas=["public"],
        tables=[table],
        profiles=[profile],
        issues=issues,
    )


def test_markdown_includes_all_required_sections():
    md = report.to_markdown(_result())
    for heading in (
        "## Database overview",
        "## Schema inventory",
        "## Table-by-table profiling",
        "## Detected relationships",
        "## Data quality warnings",
        "## Performance & indexing observations",
        "## Recommended next steps",
    ):
        assert heading in md


def test_markdown_shows_relationship_and_row_count():
    md = report.to_markdown(_result())
    assert "public.orders(customer_id) → public.customers(id)" in md
    assert "100" in md


def test_markdown_separates_quality_and_performance_issues():
    md = report.to_markdown(_result())
    quality_section = md.split("## Data quality warnings")[1].split("## Performance")[0]
    assert "missing-primary-key" in quality_section
    assert "unindexed-foreign-key" not in quality_section


def test_markdown_with_no_issues_says_none_found():
    empty = AnalysisResult(
        engine="postgres", database="docray", schemas=[], tables=[], profiles=[], issues=[]
    )
    md = report.to_markdown(empty)
    assert "None found." in md
    assert "No action items found." in md


def test_json_round_trips_core_fields():
    payload = json.loads(report.to_json(_result()))
    assert payload["engine"] == "postgres"
    assert payload["database"] == "docray"
    assert len(payload["tables"]) == 1
    assert payload["tables"][0]["name"] == "orders"
    assert len(payload["recommendations"]) == 2
