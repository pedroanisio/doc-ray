"""End-to-end model runs: assemble every layer for one set of assumptions.

:func:`run_model` is the single entry point the CLI, the sensitivity pass,
and the tests all use — one call produces the full chain

    corpus profile → table catalog → sizing → operations → scenarios →
    dynamics → maintenance

against a chosen hardware profile, with any assumption overridden via the
``overrides`` preset map.
"""

from __future__ import annotations

from dataclasses import dataclass

from capacity_model.assumptions import AssumptionRegistry
from capacity_model.catalog_tables import build_tables
from capacity_model.dynamics import ScenarioDynamics, analyze_scenario
from capacity_model.maintenance import MaintenanceResult, analyze_maintenance
from capacity_model.operations import build_operations
from capacity_model.resources import (
    ConnectionTopology,
    HardwareProfile,
    PgConfig,
    deployed_topology,
    documented_production_hardware,
    pg_config_for,
    scaled_hardware,
)
from capacity_model.schema_catalog import (
    CorpusProfile,
    TableSpec,
    build_corpus_profile,
)
from capacity_model.sizing import SizingResult, size_corpus
from capacity_model.workload import (
    OpCost,
    Scenario,
    build_scenarios,
    op_rates,
    register_unit_costs,
)

#: Candidate scaled host evaluated alongside the documented cluster.
SCALED_CORES, SCALED_RAM_GB, SCALED_DISK_TB = 48, 512, 32.0


@dataclass(frozen=True)
class ScenarioReport:
    """Dynamics + maintenance for one scenario."""

    scenario: Scenario
    dynamics: ScenarioDynamics
    maintenance: MaintenanceResult


@dataclass(frozen=True)
class ModelResult:
    """Everything one model run produced."""

    registry: AssumptionRegistry
    corpus: CorpusProfile
    tables: list[TableSpec]
    sizes: SizingResult
    hardware: HardwareProfile
    pg: PgConfig
    topology: ConnectionTopology
    operations: dict[str, OpCost]
    scenarios: list[ScenarioReport]

    def scenario(self, name: str) -> ScenarioReport:
        """Lookup one scenario report by name."""
        return next(s for s in self.scenarios if s.scenario.name == name)

    def headline_metrics(self) -> dict[str, float]:
        """The scalar outcomes the sensitivity pass measures impact against."""
        expected = self.scenario("expected")
        peak = self.scenario("peak")
        degraded = self.scenario("degraded")
        reader = next(o for o in expected.dynamics.ops if o.name == "webapp_reader_page")
        fts = next(o for o in expected.dynamics.ops if o.name == "api_full_text_search")
        return {
            "storage_tb": self.sizes.total_bytes / 1e12,
            "expected_reader_p95_s": reader.p95_response_s,
            "expected_fts_p95_s": fts.p95_response_s,
            "peak_cpu_utilization": peak.dynamics.cpu.utilization,
            "peak_saturation_multiplier": peak.dynamics.saturation_multiplier,
            "expected_wal_gb_day": expected.maintenance.wal_gb_per_day,
            "degraded_autovacuum_demand": degraded.maintenance.autovacuum_demand_fraction,
        }


def run_model(overrides: dict[str, float] | None = None, hardware: str = "prod") -> ModelResult:
    """Run the full pipeline; ``hardware`` is ``prod`` or ``scaled``."""
    reg = AssumptionRegistry(presets=dict(overrides or {}))
    corpus = build_corpus_profile(reg)
    tables = build_tables(reg, corpus)
    if hardware == "scaled":
        hw = scaled_hardware(reg, SCALED_CORES, SCALED_RAM_GB, SCALED_DISK_TB)
    elif hardware == "prod":
        hw = documented_production_hardware(reg)
    else:
        raise ValueError(f"unknown hardware profile {hardware!r} (use 'prod' or 'scaled')")
    pg = pg_config_for(reg, hw)
    topo = deployed_topology(reg)
    sizes = size_corpus(reg, corpus, tables)
    units = register_unit_costs(reg)
    ops = build_operations(reg, corpus, sizes, units)
    reports: list[ScenarioReport] = []
    for sc in build_scenarios(reg, corpus):
        rates = op_rates(sc, ops)
        dyn = analyze_scenario(reg, sc, ops, rates, hw, pg, topo, sizes)
        maint = analyze_maintenance(reg, sc, dyn, tables, sizes, pg, hw)
        reports.append(ScenarioReport(scenario=sc, dynamics=dyn, maintenance=maint))
    return ModelResult(
        registry=reg,
        corpus=corpus,
        tables=tables,
        sizes=sizes,
        hardware=hw,
        pg=pg,
        topology=topo,
        operations=ops,
        scenarios=reports,
    )
