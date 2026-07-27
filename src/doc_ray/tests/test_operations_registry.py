"""The operation registry and its eligibility SQL.

These are the guards that keep the console honest: the status view and the runner
must select on the same predicate, or the page reports a backlog that firing the
operation does not clear.
"""

from __future__ import annotations

import pytest

from doc_ray.application.operation_selection import (
    count_sql,
    eligibility_where,
    selection_sql,
    unit_label,
)
from doc_ray.domain.operations import (
    OPERATION_KEYS,
    OPERATIONS,
    UnknownOperationError,
    get_operation,
)


class TestRegistry:
    def test_every_operation_has_a_distinct_key(self) -> None:
        assert len(set(OPERATION_KEYS)) == len(OPERATION_KEYS)

    def test_lookup_returns_the_registered_operation(self) -> None:
        assert get_operation("visual_embed").job_kind == "visual_embed"

    def test_unknown_key_names_the_known_ones(self) -> None:
        with pytest.raises(UnknownOperationError) as excinfo:
            get_operation("visual_embeddings")
        assert "visual_embed" in str(excinfo.value)

    def test_backfills_never_default_to_the_foreground_priority(self) -> None:
        """Priority 100 would tie with ingest work and starve the pipeline."""
        assert all(op.default_priority > 100 for op in OPERATIONS)

    def test_only_the_vlm_tier_is_marked_expensive(self) -> None:
        expensive = {op.key for op in OPERATIONS if op.expensive}
        assert expensive == {"visual_caption"}

    def test_every_operation_declares_a_known_lane(self) -> None:
        assert all(op.lane in {"cpu", "gpu"} for op in OPERATIONS)


class TestSelectionSql:
    @pytest.mark.parametrize("key", OPERATION_KEYS)
    @pytest.mark.parametrize("only_missing", [True, False])
    def test_selection_and_count_share_the_same_predicate(
        self, key: str, only_missing: bool
    ) -> None:
        """A status count that disagrees with the runner is a lying console."""
        where = eligibility_where(key, only_missing=only_missing)
        select, _ = selection_sql(key, only_missing=only_missing)
        count, _ = count_sql(key, only_missing=only_missing)
        assert f"WHERE {where}" in select
        assert f"WHERE {where}" in count

    @pytest.mark.parametrize("key", OPERATION_KEYS)
    def test_only_missing_narrows_rather_than_replaces(self, key: str) -> None:
        wide, _ = selection_sql(key, only_missing=False)
        narrow, _ = selection_sql(key, only_missing=True)
        assert len(narrow) > len(wide)
        assert wide.split(" ORDER BY ")[0] in narrow

    @pytest.mark.parametrize("key", OPERATION_KEYS)
    def test_selection_is_deterministically_ordered(self, key: str) -> None:
        """A partially drained sweep must resume in the same order it started."""
        sql, _ = selection_sql(key, only_missing=True)
        assert sql.endswith("ORDER BY d.created_at, d.id")

    def test_only_the_semantic_selection_is_model_scoped(self) -> None:
        _, semantic = selection_sql("semantic_embed", only_missing=True)
        _, visual = selection_sql("visual_embed", only_missing=True)
        assert "model" in semantic
        # Passing an unused parameter makes psycopg raise, so it must be absent.
        assert visual == {}

    def test_unknown_operation_is_rejected_before_sql_is_built(self) -> None:
        with pytest.raises(UnknownOperationError):
            selection_sql("drop_everything", only_missing=True)

    def test_unit_labels_match_what_each_tier_actually_counts(self) -> None:
        assert unit_label("visual_embed") == "images"
        assert unit_label("semantic_embed") == "sentences"
        assert unit_label("reextract") is None

    @pytest.mark.parametrize("key", OPERATION_KEYS)
    def test_count_reports_both_documents_and_units(self, key: str) -> None:
        sql, _ = count_sql(key, only_missing=True)
        assert "AS documents" in sql
        assert "AS units" in sql
