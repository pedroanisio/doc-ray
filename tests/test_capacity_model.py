"""End-to-end tests for the capacity model: invariants, not exact numbers.

The model's absolute outputs are estimates; what must hold are the structural
properties — calibration against the repo-measured anchor, monotonicity across
scenarios, override plumbing, valid machine-readable output, and the presence
of every mandated report section.
"""

from __future__ import annotations

import json
import math

import pytest
from capacity_model.analysis import (
    capacity_margins,
    epistemic_conclusions,
    rank_bottlenecks,
    sensitivity_analysis,
)
from capacity_model.model import ModelResult, run_model
from capacity_model.report_json import build_json_report, dump_json
from capacity_model.report_md import build_markdown_report


@pytest.fixture(scope="module")
def prod() -> ModelResult:
    """One shared default run on the documented production profile."""
    return run_model()


@pytest.fixture(scope="module")
def scaled() -> ModelResult:
    """One shared run on the candidate scaled host."""
    return run_model(hardware="scaled")


class TestCalibration:
    """The bottom-up sizes must track the repo's measured per-sentence anchor."""

    def test_per_sentence_bytes_within_2x_of_measured(self, prod: ModelResult) -> None:
        """docs/production-setup.md measures ~66 KB/annotated sentence all-in."""
        assert 0.5 < prod.sizes.calibration_ratio < 2.0

    def test_total_storage_is_terabyte_scale(self, prod: ModelResult) -> None:
        """1M mixed docs with full annotation must land in single-digit-to-tens TB."""
        tb = prod.sizes.total_bytes / 1e12
        assert 3.0 < tb < 60.0

    def test_annotation_tables_dominate(self, prod: ModelResult) -> None:
        """tokens must be the largest table — 6 indexes over 8.5B rows."""
        largest = max(prod.sizes.tables, key=lambda t: t.total_bytes)
        assert largest.name == "tokens"
        assert largest.total_index_bytes > largest.heap_bytes


class TestScenarioMonotonicity:
    """More load must never produce less pressure."""

    def test_qps_increases_with_scenario_intensity(self, prod: ModelResult) -> None:
        """baseline < expected < peak in statement rate."""
        qps = [rep.dynamics.total_qps for rep in prod.scenarios[:3]]
        assert qps[0] < qps[1] < qps[2]

    def test_margins_shrink_with_load(self, prod: ModelResult) -> None:
        """Saturation multiplier must be non-increasing baseline → peak."""
        mults = [rep.dynamics.saturation_multiplier for rep in prod.scenarios[:3]]
        assert mults[0] >= mults[1] >= mults[2]

    def test_wal_grows_with_pipeline_load(self, prod: ModelResult) -> None:
        """The degraded reannotation wave must produce the most WAL."""
        wal = {rep.scenario.name: rep.maintenance.total_wal_mb_s for rep in prod.scenarios}
        assert wal["degraded"] > wal["expected"] > wal["baseline"]

    def test_prod_host_saturates_before_scaled(
        self, prod: ModelResult, scaled: ModelResult
    ) -> None:
        """16 GB cannot cache a multi-TB corpus; 512 GB must do strictly better."""
        for p_rep, s_rep in zip(prod.scenarios, scaled.scenarios, strict=True):
            assert s_rep.dynamics.saturation_multiplier >= p_rep.dynamics.saturation_multiplier


class TestOverrides:
    """Every assumption must be overridable through the preset map."""

    def test_doubling_corpus_scales_storage(self, prod: ModelResult) -> None:
        """2M documents ≈ 2× bytes (sublinear only via vocabulary tables)."""
        big = run_model({"corpus.documents": 2_000_000})
        ratio = big.sizes.total_bytes / prod.sizes.total_bytes
        assert 1.8 < ratio < 2.2

    def test_override_is_recorded_with_provenance(self) -> None:
        """A preset must be visible in the ledger with the replaced default."""
        res = run_model({"behavior.expected_ingest_docs_per_day": 5000})
        entry = res.registry.entries["behavior.expected_ingest_docs_per_day"]
        assert entry.value == 5000
        assert "override" in entry.citation

    def test_unknown_hardware_rejected(self) -> None:
        """Hardware profile names are a closed set."""
        with pytest.raises(ValueError):
            run_model(hardware="mainframe")


class TestAnalysis:
    """Bottlenecks, margins, sensitivity, and epistemic labels."""

    def test_bottlenecks_sorted_desc(self, prod: ModelResult) -> None:
        """Ranking must be most-saturated-first."""
        utils = [b.utilization for b in rank_bottlenecks(prod)]
        assert utils == sorted(utils, reverse=True)

    def test_margin_binding_resource_is_finite(self, prod: ModelResult) -> None:
        """Binding utilization must never be inf (saturation is attributed
        to the saturated station, not the downstream pool)."""
        for row in capacity_margins(prod):
            assert math.isfinite(float(str(row["binding_utilization"])))

    def test_sensitivity_ranked_and_covers_doc_mix(self, prod: ModelResult) -> None:
        """The pass must run, rank descending, and include the corpus mix."""
        rows = sensitivity_analysis(prod, "prod")
        assert rows, "no sensitivity-enabled assumptions found"
        scores = [r.impact_score for r in rows]
        assert scores == sorted(scores, reverse=True)
        assert any("corpus.mix" in r.name or "sentences_per_doc" in r.name for r in rows)

    def test_epistemic_conclusions_cover_both_kinds(self, prod: ModelResult) -> None:
        """There must be analytical AND empirical-validation conclusions."""
        statuses = {c["status"] for c in epistemic_conclusions(prod)}
        assert any("analytical" in s for s in statuses)
        assert any("empirical" in s for s in statuses)


class TestReports:
    """Machine-readable and human-readable outputs."""

    def test_json_report_is_strict_json(self, prod: ModelResult, scaled: ModelResult) -> None:
        """No Infinity/NaN literals; round-trips through json.loads."""
        text = dump_json(build_json_report(prod, scaled, [], "2026-07-25"))
        obj = json.loads(text)
        assert obj["meta"]["target_scale"]["documents"] == 1_000_000
        assert obj["assumptions"], "assumption ledger missing"
        assert len(obj["scenarios"]["primary"]) == 4

    def test_markdown_report_has_all_mandated_sections(
        self, prod: ModelResult, scaled: ModelResult
    ) -> None:
        """All ten deliverable sections + disclaimer frontmatter must exist."""
        md = build_markdown_report(prod, scaled, [], "2026-07-25")
        assert md.startswith("---\ndisclaimer:")
        for heading in (
            "## 1. Estimated table and index sizes",
            "## 2. Queries and transactions per second",
            "## 3. Connection and concurrency requirements",
            "## 4. Estimated latency by operation type",
            "## 5. Likely bottlenecks and saturation points",
            "## 6. Cardinality and data-distribution effects",
            "## 7. Vacuum, dead tuples, WAL, and checkpoint pressure",
            "## 8. Capacity margins by scenario",
            "## 9. Assumptions with the greatest effect",
            "## 10. Analytical estimates vs conclusions requiring",
            "## Appendix A. Full assumption ledger",
        ):
            assert heading in md, f"missing section: {heading}"

    def test_every_assumption_has_citation_and_source(self, prod: ModelResult) -> None:
        """No hidden defaults: every input carries a non-empty citation."""
        for a in prod.registry.entries.values():
            assert a.citation.strip(), f"{a.name} has no citation"
            assert a.rationale.strip(), f"{a.name} has no rationale"


class TestMaintenance:
    """Vacuum/WAL mechanics."""

    def test_autovacuum_override_tables_trigger_more_often(self, prod: ModelResult) -> None:
        """Migration-034 tables (scale 0.02) must trigger far earlier than 0.2
        default tables of similar size under the same churn."""
        degraded = prod.scenario("degraded").maintenance
        by_name = {t.name: t for t in degraded.tables}
        assert "tokens" in by_name and "token_feature" in by_name
        tokens_rel = by_name["tokens"].trigger_rows / prod.sizes.table("tokens").rows
        feat_rel = by_name["token_feature"].trigger_rows / prod.sizes.table("token_feature").rows
        assert tokens_rel < feat_rel / 5

    def test_checkpoints_forced_under_pipeline_load(self, prod: ModelResult) -> None:
        """Default max_wal_size=1GB must force size-based checkpoints in the
        degraded scenario's WAL rate."""
        assert prod.scenario("degraded").maintenance.checkpoint_forced_by_size
