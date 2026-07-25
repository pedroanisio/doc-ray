"""Bottleneck ranking, capacity margins, sensitivity, and epistemic labeling.

The sensitivity pass is brute-force and honest: every assumption that opted
in (``sensitivity_key``) is halved and doubled, the *entire* model re-runs,
and the impact score is the largest |log2 ratio| across the headline metrics.
That makes "which assumptions matter most" a computed output, not an opinion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from capacity_model.model import ModelResult, run_model


@dataclass(frozen=True)
class Bottleneck:
    """One resource's proximity to saturation in one scenario."""

    scenario: str
    resource: str
    utilization: float
    note: str


@dataclass(frozen=True)
class SensitivityRow:
    """Impact of halving/doubling one assumption on the headline metrics."""

    name: str
    default: float
    unit: str
    impact_score: float
    worst_metric: str
    at_half: float
    at_double: float


def rank_bottlenecks(res: ModelResult) -> list[Bottleneck]:
    """All resource utilizations across scenarios, most saturated first."""
    out: list[Bottleneck] = []
    for rep in res.scenarios:
        d = rep.dynamics
        m = rep.maintenance
        breaches = [o.name for o in d.ops if o.breaches_statement_timeout]
        out.extend(
            [
                Bottleneck(
                    d.scenario,
                    "cpu",
                    d.cpu.utilization,
                    f"{res.hardware.cores} vCPU; mean query service {d.cpu.service_s * 1e3:.1f} ms",
                ),
                Bottleneck(
                    d.scenario,
                    "disk-read",
                    d.disk_read.utilization,
                    f"{d.disk_read.arrival_rate:,.0f} random reads/s of "
                    f"{res.hardware.disk_read_iops:,.0f} IOPS",
                ),
                Bottleneck(
                    d.scenario,
                    "disk-write+wal",
                    d.disk_write_util,
                    f"WAL {m.total_wal_mb_s:.1f} MB/s + checkpoint "
                    f"{m.checkpoint_write_mb_s:.0f} MB/s",
                ),
                Bottleneck(
                    d.scenario,
                    "api-pool",
                    d.pool_pressure["api_pool_utilization"],
                    f"needs {d.api_backends_needed:.1f} of "
                    f"{res.topology.api_backends_max} pooled backends; one "
                    "mcp briefing_pack can borrow 61 concurrently vs pool max 10/worker",
                ),
                Bottleneck(
                    d.scenario,
                    "max_connections",
                    d.pool_pressure["max_connections_utilization"],
                    f"{res.topology.total_max_backends()} worst-case app backends of "
                    f"{res.pg.max_connections}",
                ),
                Bottleneck(
                    d.scenario,
                    "autovacuum",
                    m.autovacuum_demand_fraction,
                    "fraction of the 100k-points/s cluster vacuum budget consumed",
                ),
                Bottleneck(
                    d.scenario,
                    "statement_timeout",
                    1.0 if breaches else 0.0,
                    ("p95 > 15 s for: " + ", ".join(breaches))
                    if breaches
                    else "no modeled op breaches the 15 s API statement_timeout",
                ),
            ]
        )
    return sorted(out, key=lambda b: -b.utilization)


def capacity_margins(res: ModelResult) -> list[dict[str, object]]:
    """Per-scenario headroom: how much load growth until the first wall."""
    rows: list[dict[str, object]] = []
    for rep in res.scenarios:
        d = rep.dynamics
        candidates = [
            ("cpu", d.cpu.utilization),
            ("disk-read", d.disk_read.utilization),
            ("disk-write+wal", d.disk_write_util),
            ("api-pool", d.pool_pressure["api_pool_utilization"]),
        ]
        finite = [c for c in candidates if math.isfinite(c[1])]
        binding = max(finite, key=lambda kv: kv[1])
        rows.append(
            {
                "scenario": d.scenario,
                "binding_resource": binding[0],
                "binding_utilization": binding[1],
                "saturation_multiplier": d.saturation_multiplier,
                "headroom_pct": max(0.0, (1.0 - binding[1]) * 100.0),
            }
        )
    return rows


def _clamp(x: float) -> float:
    """Clamp metric values so saturated (inf) results stay comparable."""
    if math.isnan(x):
        return 1e9
    return min(max(x, 1e-9), 1e9)


def sensitivity_analysis(base: ModelResult, hardware: str) -> list[SensitivityRow]:
    """Halve/double every opted-in assumption; rank by headline-metric impact."""
    base_metrics = {k: _clamp(v) for k, v in base.headline_metrics().items()}
    rows: list[SensitivityRow] = []
    for a in base.registry.sensitivity_candidates():
        impacts: dict[str, float] = {}
        by_factor: dict[float, float] = {}
        for factor in (0.5, 2.0):
            result = run_model({a.name: a.value * factor}, hardware=hardware)
            metrics = result.headline_metrics()
            worst = 0.0
            for key, val in metrics.items():
                ratio = _clamp(val) / base_metrics[key]
                score = abs(math.log2(ratio)) if ratio > 0 else 0.0
                if score > worst:
                    worst = score
                impacts[key] = max(impacts.get(key, 0.0), score)
            by_factor[factor] = worst
        worst_metric = max(impacts, key=lambda k: impacts[k])
        rows.append(
            SensitivityRow(
                name=a.name,
                default=a.value,
                unit=a.unit,
                impact_score=max(by_factor.values()),
                worst_metric=worst_metric,
                at_half=by_factor[0.5],
                at_double=by_factor[2.0],
            )
        )
    return sorted(rows, key=lambda r: -r.impact_score)


def epistemic_conclusions(res: ModelResult) -> list[dict[str, str]]:
    """Label each class of conclusion: analytical vs requires empirical validation."""
    calib = res.sizes.calibration_ratio
    return [
        {
            "conclusion": "Table/index storage totals and per-table breakdown",
            "status": "analytical",
            "why": (
                "Arithmetic over the documented page format and repo-measured "
                "per-sentence row ratios; bottom-up per-annotated-sentence bytes land "
                f"at {calib:.2f}× the repo's own measured 66 KB/sentence anchor "
                "(docs/production-setup.md:51-77). The document-length mix that scales "
                "them to 1M documents is, however, an assumption."
            ),
        },
        {
            "conclusion": "QPS/TPS, WAL volume, connection arithmetic",
            "status": "analytical",
            "why": (
                "Direct products of stated behavior rates and code-derived statement "
                "counts (pool sizes, statements per op are repo facts). The behavior "
                "rates themselves are assumptions exposed as inputs."
            ),
        },
        {
            "conclusion": "Absolute latencies (means, p95s)",
            "status": "requires-empirical-validation",
            "why": (
                "Built on assumed per-tuple/per-page CPU costs and cloud-disk latency, "
                "then M/M/c queueing (exponential service). Use them comparatively "
                "(op A vs op B, scenario X vs Y), not as SLO predictions; validate "
                "with pg_stat_statements + EXPLAIN (ANALYZE, BUFFERS) on a scaled sample."
            ),
        },
        {
            "conclusion": "Bottleneck ordering and saturation multipliers",
            "status": "analytical-ordering / empirical-absolutes",
            "why": (
                "Which resource saturates first is robust across wide input ranges "
                "(see the sensitivity table); the exact multiplier inherits the unit-cost "
                "uncertainty."
            ),
        },
        {
            "conclusion": "RLS InitPlan cost at org-visibility scale",
            "status": "requires-empirical-validation",
            "why": (
                "docray_readable_documents() materializing the tenant corpus per query "
                "is a code fact (migration 110); its per-uuid cost is assumed. Validate "
                "with EXPLAIN ANALYZE on a synthetic 100k-doc tenant before trusting "
                "read-path latencies at scale."
            ),
        },
        {
            "conclusion": "Autovacuum debt during reannotation waves",
            "status": "analytical",
            "why": (
                "Cost-based pacing (100k points/s cluster budget) and index-scan volumes "
                "follow from documented vacuum mechanics plus computed index sizes; the "
                "wave rate is an explicit input."
            ),
        },
        {
            "conclusion": "HNSW residency requirement and semantic-search latency",
            "status": "requires-empirical-validation",
            "why": (
                "Index size is near-analytical (pgvector storage formula); the probe "
                "count per search and recall/latency trade-off need a pgvector benchmark "
                "at representative row counts."
            ),
        },
        {
            "conclusion": "Cache-miss ratios per region",
            "status": "requires-empirical-validation",
            "why": (
                "The waterfall cache model and the hot-document skew (5% of docs take "
                "80% of reads) are stated simplifications; validate against "
                "pg_stat_io / pg_buffercache on production."
            ),
        },
    ]
