"""Unit tests for the pipeline cost-telemetry model (scripts/cost_telemetry.py).

The measurement queries need a live corpus; the projection and costing math does
not, so everything below runs on synthetic StageModel/CorpusProfile values.
"""

from __future__ import annotations

import math

import cost_telemetry as ct
import pytest


def _stage(kind: str, lane: str, fixed: float, per_sentence: float, n: int = 100) -> ct.StageModel:
    return ct.StageModel(
        kind=kind,
        lane=lane,
        n=n,
        fixed_s=fixed,
        per_sentence_s=per_sentence,
        correlation=0.9,
        p50_s=fixed,
        p90_s=fixed * 2,
        observed_hours=1.0,
    )


def _profile(documents: int = 100, sentences: int = 400_000) -> ct.CorpusProfile:
    return ct.CorpusProfile(
        documents=documents,
        sentences=sentences,
        source_bytes=documents * 15_000_000,
        db_bytes=sentences * 38_000,
    )


def test_stage_seconds_is_fixed_plus_per_sentence() -> None:
    model = _stage("annotate", ct.LANE_GPU, fixed=5.26, per_sentence=0.01759)
    assert ct.stage_seconds(model, 4150) == pytest.approx(5.26 + 0.01759 * 4150)


def test_stage_seconds_never_returns_negative_for_a_noisy_fit() -> None:
    # A negative intercept is a legitimate least-squares outcome on a short
    # sample; a negative *duration* is not, and would silently credit time back
    # to the projection.
    model = _stage("canonicalize", ct.LANE_CPU, fixed=-40.0, per_sentence=0.001)
    assert ct.stage_seconds(model, 100) == 0.0


def test_project_splits_hours_by_lane() -> None:
    models = [
        _stage("annotate", ct.LANE_GPU, fixed=0.0, per_sentence=0.018),
        _stage("canonicalize", ct.LANE_CPU, fixed=0.0, per_sentence=0.003),
    ]
    profile = _profile(documents=100, sentences=400_000)  # 4000 sentences/doc

    projected = ct.project(models, profile, documents=1000)

    # 1000 docs x 4000 sentences = 4M sentences through each stage.
    assert projected.lane_hours[ct.LANE_GPU] == pytest.approx(4_000_000 * 0.018 / 3600)
    assert projected.lane_hours[ct.LANE_CPU] == pytest.approx(4_000_000 * 0.003 / 3600)
    assert projected.sentences == 4_000_000


def test_project_scales_storage_linearly_from_measured_per_document_bytes() -> None:
    profile = _profile(documents=100, sentences=400_000)

    projected = ct.project([], profile, documents=500)

    assert projected.object_gb == pytest.approx(500 * 15_000_000 / ct.BYTES_PER_GB)
    assert projected.db_gb == pytest.approx(profile.db_bytes / 100 * 500 / ct.BYTES_PER_GB)


def test_project_on_an_empty_corpus_yields_zeroes_not_a_divide_by_zero() -> None:
    projected = ct.project([], ct.CorpusProfile(0, 0, 0, 0), documents=1000)

    assert projected.sentences == 0
    assert projected.db_gb == 0.0
    assert projected.object_gb == 0.0


def test_estimate_cost_prices_each_lane_and_storage_line() -> None:
    projected = ct.Projection(
        documents=1000,
        sentences=4_000_000,
        lane_hours={ct.LANE_GPU: 20.0, ct.LANE_CPU: 4.0},
        db_gb=100.0,
        object_gb=15.0,
    )
    rates = ct.Rates(
        gpu_host_hourly=2.0, cpu_host_hourly=0.5, db_gb_month=0.20, object_gb_month=0.02
    )

    cost = ct.estimate_cost(projected, rates, months=1.0)

    assert cost.lines["gpu_compute"] == pytest.approx(40.0)
    assert cost.lines["cpu_compute"] == pytest.approx(2.0)
    assert cost.lines["db_storage"] == pytest.approx(20.0)
    assert cost.lines["object_storage"] == pytest.approx(0.3)
    assert cost.total == pytest.approx(62.3)
    assert cost.unpriced == []


def test_estimate_cost_reports_missing_rates_instead_of_inventing_them() -> None:
    # A rate nobody supplied is not zero — pricing it as zero would understate
    # the total silently. The line is dropped and named in `unpriced`.
    projected = ct.Projection(1000, 4_000_000, {ct.LANE_GPU: 20.0, ct.LANE_CPU: 4.0}, 100.0, 15.0)
    rates = ct.Rates(
        gpu_host_hourly=2.0, cpu_host_hourly=None, db_gb_month=None, object_gb_month=0.02
    )

    cost = ct.estimate_cost(projected, rates, months=1.0)

    assert cost.lines["gpu_compute"] == pytest.approx(40.0)
    assert "cpu_compute" not in cost.lines
    assert set(cost.unpriced) == {"cpu_compute", "db_storage"}
    assert cost.total == pytest.approx(40.3)
    assert cost.complete is False


def test_estimate_cost_is_complete_only_when_every_line_is_priced() -> None:
    projected = ct.Projection(1, 1, {ct.LANE_GPU: 1.0, ct.LANE_CPU: 1.0}, 1.0, 1.0)
    rates = ct.Rates(1.0, 1.0, 1.0, 1.0)

    assert ct.estimate_cost(projected, rates, months=1.0).complete is True


def test_storage_is_a_recurring_line_and_compute_is_not() -> None:
    # Compute is paid once per document processed; storage is paid every month
    # the corpus exists. Doubling the horizon must double only the storage side.
    projected = ct.Projection(1000, 4_000_000, {ct.LANE_GPU: 10.0, ct.LANE_CPU: 0.0}, 100.0, 10.0)
    rates = ct.Rates(
        gpu_host_hourly=2.0, cpu_host_hourly=1.0, db_gb_month=0.10, object_gb_month=0.02
    )

    one = ct.estimate_cost(projected, rates, months=1.0)
    twelve = ct.estimate_cost(projected, rates, months=12.0)

    assert twelve.lines["gpu_compute"] == pytest.approx(one.lines["gpu_compute"])
    assert twelve.lines["db_storage"] == pytest.approx(one.lines["db_storage"] * 12)


def test_lane_for_kind_follows_the_worker_pool_split() -> None:
    # The GPU pool is exactly the set worker-fast excludes (LONG_RUNNING_JOB_KINDS);
    # anything else lands on the CPU lane.
    assert ct.lane_for_kind("annotate") == ct.LANE_GPU
    assert ct.lane_for_kind("visual_embed") == ct.LANE_GPU
    assert ct.lane_for_kind("canonicalize") == ct.LANE_CPU
    assert ct.lane_for_kind("semantic_enrich") == ct.LANE_CPU


def test_fit_recovers_a_known_linear_relationship() -> None:
    # secs = 5 + 0.02 * sentences, exactly.
    samples = [(float(s), 5.0 + 0.02 * s) for s in (100, 500, 1000, 5000, 10000)]

    fixed, per_sentence, corr = ct.fit_linear(samples)

    assert fixed == pytest.approx(5.0)
    assert per_sentence == pytest.approx(0.02)
    assert corr == pytest.approx(1.0)


def test_fit_falls_back_to_a_flat_mean_when_the_driver_does_not_vary() -> None:
    # Every document the same size: the slope is unidentifiable, so the honest
    # model is a constant cost, not a fitted line through one x-value.
    samples = [(1000.0, 30.0), (1000.0, 34.0), (1000.0, 32.0)]

    fixed, per_sentence, corr = ct.fit_linear(samples)

    assert fixed == pytest.approx(32.0)
    assert per_sentence == 0.0
    assert math.isnan(corr) or corr == 0.0
