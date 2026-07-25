"""Throughput, cache behavior, latency, and connection pressure per scenario.

Chain: operation rates (:mod:`capacity_model.workload`) × per-operation
demands → aggregate CPU/disk/WAL load → M/M/c stations
(:mod:`capacity_model.queueing`) → per-operation response times → Little's-law
connection concurrency, checked against the deployed pool topology
(:mod:`capacity_model.resources`).

Cache model: a priority waterfall. Available cache = shared_buffers + OS page
cache (the ``effective_cache_size`` convention, runtime-config-query.html).
Regions are filled in the order dimension tables → hot document slice → FTS
GIN → HNSW → full sentence set; each region's miss probability is the
uncovered fraction. This is deliberately simple and stated as such — real
eviction is LRU-approximate per 8 kB page, so these are first-order numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from capacity_model.assumptions import AssumptionRegistry, Epistemic, Source
from capacity_model.queueing import StationResult, mmc_station
from capacity_model.resources import ConnectionTopology, HardwareProfile, PgConfig
from capacity_model.sizing import SizingResult
from capacity_model.workload import OpCost, Scenario


@dataclass(frozen=True)
class OpOutcome:
    """Per-operation modeled latency and physical demand at a scenario's load."""

    name: str
    rate: float
    cpu_s: float
    io_reads: float
    mean_response_s: float
    p95_response_s: float
    breaches_statement_timeout: bool


@dataclass(frozen=True)
class ScenarioDynamics:
    """All dynamic results for one scenario."""

    scenario: str
    total_qps: float
    total_tps: float
    read_qps: float
    write_rows_s: float
    wal_bytes_s: float
    commits_s: float
    cpu: StationResult
    disk_read: StationResult
    disk_write_util: float
    region_miss: dict[str, float]
    ops: list[OpOutcome]
    api_backends_needed: float
    webapp_backends_needed: float
    worker_backends: int
    total_backends_needed: float
    max_backends_available: int
    pool_pressure: dict[str, float]
    saturation_multiplier: float


def region_sizes(sizes: SizingResult) -> dict[str, float]:
    """Bytes per cache region, from the sizing projection."""
    ws = sizes.hot_working_set_bytes
    t = {x.name: x for x in sizes.tables}

    def total(name: str) -> float:
        return t[name].total_bytes if name in t else 0.0

    return {
        "dim": ws.get("dimension_tables", 0.0) + ws.get("btree_upper_levels", 0.0),
        "hot": ws.get("hot_document_slice", 0.0),
        "gin": (
            t["document_content"].index_bytes.get("idx_content_fts", 0.0)
            + t["document_content"].toast_bytes * 0.4
            if "document_content" in t
            else 0.0
        ),
        "hnsw": ws.get("hnsw_index_resident", 0.0),
        "sentences": total("sentences") + total("tokens") * 0.1,
    }


def cache_miss_by_region(
    reg: AssumptionRegistry,
    hw: HardwareProfile,
    pg: PgConfig,
    sizes: SizingResult,
    cold: bool,
) -> dict[str, float]:
    """Waterfall cache allocation → per-region miss probability."""
    os_cache_frac = reg.define(
        "cache.os_page_cache_fraction",
        0.75,
        "fraction of RAM",
        Source.PG_DOCS,
        "https://www.postgresql.org/docs/current/runtime-config-query.html "
        "(effective_cache_size convention: shared_buffers + kernel cache)",
        "Fraction of host RAM usable as combined database cache.",
    ).value
    cache = hw.ram_bytes * os_cache_frac
    if cold:
        cache *= reg.define(
            "cache.cold_fraction",
            0.25,
            "fraction",
            Source.ASSUMED,
            "conservative engineering assumption (first minutes after restart/failover)",
            "Usable cache coverage during the degraded/cold scenario.",
            Epistemic.EMPIRICAL,
        ).value
    miss: dict[str, float] = {}
    remaining = cache
    for name, size in region_sizes(sizes).items():
        if size <= 0:
            miss[name] = 0.0
            continue
        covered = min(size, remaining)
        remaining -= covered
        miss[name] = 1.0 - covered / size
    _ = pg  # shared_buffers is inside the RAM budget; kept for signature clarity
    return miss


def _disk_read_station(rate: float, hw: HardwareProfile) -> StationResult:
    """Disk modeled as M/M/c: c = sustainable queue depth = IOPS × latency."""
    servers = max(1, int(hw.disk_read_iops * hw.disk_random_latency_s))
    return mmc_station("disk-read", rate, hw.disk_random_latency_s, servers)


def analyze_scenario(
    reg: AssumptionRegistry,
    sc: Scenario,
    ops: dict[str, OpCost],
    rates: dict[str, float],
    hw: HardwareProfile,
    pg: PgConfig,
    topo: ConnectionTopology,
    sizes: SizingResult,
) -> ScenarioDynamics:
    """Solve one scenario end-to-end."""
    flush_s = reg.define(
        "wal.flush_latency_s",
        0.001,
        "s",
        Source.ASSUMED,
        "conservative engineering assumption (cloud SSD fsync ~0.5-2 ms)",
        "Latency of one WAL flush; synchronous_commit is at its default (on).",
        Epistemic.EMPIRICAL,
        sensitivity_key="wal_flush_latency",
    ).value
    miss = cache_miss_by_region(reg, hw, pg, sizes, sc.cold_cache)

    cpu_demand = sum(rates[k] * ops[k].cpu_s for k in rates)
    io_rate = sum(rates[k] * ops[k].pages * miss[ops[k].region] for k in rates)
    wal_rate = sum(rates[k] * ops[k].wal_bytes for k in rates)
    commits = sum(rates[k] * ops[k].commits for k in rates)
    write_rows = sum(rates[k] * ops[k].rows_written for k in rates)
    stmts = sum(rates[k] * ops[k].statements for k in rates)
    tps = sum(rates[k] * (ops[k].commits if ops[k].is_write else ops[k].statements) for k in rates)
    total_rate = sum(rates.values())
    mean_cpu = cpu_demand / total_rate if total_rate else 0.0

    cpu_station = mmc_station("cpu", total_rate, mean_cpu, hw.cores)
    disk_station = _disk_read_station(io_rate, hw)
    # WAL flushes group-commit: device sees min(commits, 1/flush) flushes/s.
    flush_rate = min(commits, 1.0 / flush_s)
    disk_write_util = wal_rate / (hw.disk_seq_mbps * 1e6) + flush_rate / hw.disk_write_iops

    outcomes: list[OpOutcome] = []
    for name, rate in sorted(rates.items()):
        op = ops[name]
        reads = op.pages * miss[op.region]
        io_time = reads * disk_station.mean_response_s
        base = op.cpu_s + io_time + op.commits * flush_s
        mean = cpu_station.mean_wait_s * (op.cpu_s / mean_cpu if mean_cpu else 1.0) + base
        p95 = (
            (cpu_station.p95_response_s - cpu_station.service_s)
            * (op.cpu_s / mean_cpu if mean_cpu else 1.0)
            + base
            + reads * (disk_station.p95_response_s - disk_station.service_s) * 0.1
        )
        outcomes.append(
            OpOutcome(
                name=name,
                rate=rate,
                cpu_s=op.cpu_s,
                io_reads=reads,
                mean_response_s=mean,
                p95_response_s=p95,
                breaches_statement_timeout=(not op.is_write and p95 > pg.statement_timeout_s),
            )
        )

    by_name = {o.name: o for o in outcomes}
    api_ops = [n for n in rates if n.startswith(("api_", "mcp_"))]
    web_ops = [n for n in rates if n.startswith("webapp_")]
    api_conc = sum(rates[n] * by_name[n].mean_response_s for n in api_ops)
    web_conc = sum(rates[n] * (by_name[n].mean_response_s + 2 * 0.003) for n in web_ops)
    total_needed = api_conc + web_conc + topo.worker_backends() + topo.overhead_connections
    pool_pressure = {
        "api_pool_utilization": api_conc / max(topo.api_backends_max, 1),
        "webapp_thread_utilization": web_conc / max(topo.webapp_backends_max, 1),
        "max_connections_utilization": (topo.total_max_backends() / max(pg.max_connections, 1)),
        "mcp_briefing_pool_borrows": 61.0,
    }
    # Saturation is judged on station utilizations (finite, ∝ load); pool
    # pressure joins only while finite — an ∞ pool need is a *consequence* of
    # an already-saturated station, not the binding constraint itself.
    utils = [cpu_station.utilization, disk_station.utilization, disk_write_util]
    pool_util = api_conc / max(topo.api_backends_max, 1)
    if math.isfinite(pool_util):
        utils.append(pool_util)
    peak_util = max(utils)
    return ScenarioDynamics(
        scenario=sc.name,
        total_qps=stmts,
        total_tps=tps,
        read_qps=sum(rates[k] * ops[k].statements for k in rates if not ops[k].is_write),
        write_rows_s=write_rows,
        wal_bytes_s=wal_rate,
        commits_s=commits,
        cpu=cpu_station,
        disk_read=disk_station,
        disk_write_util=disk_write_util,
        region_miss=miss,
        ops=outcomes,
        api_backends_needed=api_conc,
        webapp_backends_needed=web_conc,
        worker_backends=topo.worker_backends(),
        total_backends_needed=total_needed,
        max_backends_available=pg.max_connections,
        pool_pressure=pool_pressure,
        saturation_multiplier=(1.0 / peak_util) if peak_util > 0 else float("inf"),
    )
