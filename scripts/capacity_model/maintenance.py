"""Vacuum, dead tuples, WAL, checkpoint, and replication pressure per scenario.

Sources for the mechanics:

* Autovacuum triggering: vacuum threshold = ``autovacuum_vacuum_threshold`` +
  ``autovacuum_vacuum_scale_factor`` × reltuples
  (https://www.postgresql.org/docs/current/routine-vacuuming.html). Migration
  ``034_annotation_table_autovacuum.sql`` overrides scale to 0.02 / threshold
  1000 on tokens, dependency, lexeme_occurrences, entity_mention only.
* Cost-based pacing: autovacuum accrues cost points (hit 1 / miss 2 / dirty 20,
  runtime-config-resource.html) and sleeps ``autovacuum_vacuum_cost_delay``
  (2 ms) per ``vacuum_cost_limit`` (200) points → a cluster-wide budget of
  limit/delay = 100,000 points/s **shared across all workers**
  (runtime-config-autovacuum.html: "the cost limit is distributed
  proportionally among the running workers").
* Index vacuuming scans every index page of the table whenever enough dead
  tuples exist (PG14+ skips it below ~2% of heap tuples —
  routine-vacuuming.html / vacuum_index_cleanup) — at doc-ray's fact-table
  index sizes this dominates vacuum cost.
* Checkpoints & full-page images: after each checkpoint, the first change to
  a page writes the whole page into WAL (``full_page_writes``,
  runtime-config-wal.html; wal-configuration.html). Short checkpoint
  intervals therefore amplify WAL — modeled as a fixed-point iteration.

Reannotation churn is doc-ray-specific: re-annotating a document DELETEs and
re-COPYs its layer rows (adapters/annotation_spacy.py:458-556), so dead-tuple
rate scales with the reannotation wave, exactly the pattern that motivated
migration 034.
"""

from __future__ import annotations

from dataclasses import dataclass

from capacity_model import pg_constants as pgc
from capacity_model.assumptions import AssumptionRegistry, Epistemic, Source
from capacity_model.dynamics import ScenarioDynamics
from capacity_model.resources import HardwareProfile, PgConfig
from capacity_model.schema_catalog import TableSpec
from capacity_model.sizing import SizingResult
from capacity_model.workload import Scenario

#: Cluster-wide autovacuum throughput: vacuum_cost_limit / cost_delay points/s.
AV_POINTS_PER_S = pgc.DEFAULT_VACUUM_COST_LIMIT / pgc.DEFAULT_AV_COST_DELAY_S


@dataclass(frozen=True)
class TableVacuum:
    """Autovacuum behavior of one churn table under a scenario."""

    name: str
    dead_rows_per_day: float
    trigger_rows: float
    cycles_per_day: float
    vacuum_hours_per_cycle: float
    vacuum_hours_per_day: float
    steady_dead_fraction: float


@dataclass(frozen=True)
class MaintenanceResult:
    """WAL/checkpoint/vacuum posture for one scenario."""

    scenario: str
    base_wal_mb_s: float
    fpi_wal_mb_s: float
    total_wal_mb_s: float
    wal_gb_per_day: float
    checkpoint_interval_s: float
    checkpoint_forced_by_size: bool
    checkpoint_write_mb_s: float
    tables: list[TableVacuum]
    autovacuum_demand_fraction: float
    autovacuum_keeping_up: bool
    replication_note: str


def _churn_rows_per_day(spec: TableSpec, sc: Scenario, jobs_updates_per_day: float) -> float:
    """Dead tuples/day: reannotation delete-side plus jobs-table update churn."""
    dead = sc.reannotate_docs_per_day * spec.reannotate_rows_per_doc
    if spec.name == "jobs":
        dead += jobs_updates_per_day
    return dead


def _vacuum_cost_points(spec_pages: float, index_pages: float, dirty_pages: float) -> float:
    """Cost points for one vacuum cycle (miss-dominated heap+index scan)."""
    return (
        spec_pages * pgc.VACUUM_COST_PAGE_MISS
        + index_pages * pgc.VACUUM_COST_PAGE_MISS
        + dirty_pages * pgc.VACUUM_COST_PAGE_DIRTY
    )


def analyze_maintenance(
    reg: AssumptionRegistry,
    sc: Scenario,
    dyn: ScenarioDynamics,
    tables: list[TableSpec],
    sizes: SizingResult,
    pg: PgConfig,
    hw: HardwareProfile,
) -> MaintenanceResult:
    """Solve vacuum cadence and the WAL/checkpoint fixed point for one scenario."""
    fpi_bytes = reg.define(
        "wal.fpi_avg_bytes",
        6144,
        "bytes/page image",
        Source.PG_DOCS,
        "https://www.postgresql.org/docs/current/runtime-config-wal.html "
        "(full_page_writes; the unused 'hole' is omitted from the image)",
        "Average full-page-image size after hole removal on ~25%-free pages.",
        Epistemic.EMPIRICAL,
        sensitivity_key="fpi_bytes",
    ).value
    rnd_idx = reg.define(
        "wal.random_index_pages_per_row",
        2.2,
        "pages/row",
        Source.ASSUMED,
        "conservative engineering assumption: uuid keys (gen_random_uuid) scatter "
        "across leaves while ulid keys append rightmost (db/migrations/002:8)",
        "Distinct index leaf pages dirtied per written row (drives FPI volume).",
        Epistemic.EMPIRICAL,
        sensitivity_key="random_index_pages",
    ).value

    # --- WAL / checkpoint fixed point ---------------------------------------
    base_wal = dyn.wal_bytes_s
    dirty_pages_s = dyn.write_rows_s * rnd_idx + dyn.write_rows_s / 50.0
    wal_rate = base_wal
    interval = pg.checkpoint_timeout_s
    forced = False
    for _ in range(6):
        by_size = (
            pg.max_wal_size_bytes
            / (1.0 + pgc.DEFAULT_CHECKPOINT_COMPLETION_TARGET)
            / max(wal_rate, 1.0)
        )
        interval = min(pg.checkpoint_timeout_s, by_size)
        forced = by_size < pg.checkpoint_timeout_s
        # First touch of each distinct page per cycle emits one FPI.
        fpi_rate = min(dirty_pages_s, dirty_pages_s * 1.0) * fpi_bytes
        # Long intervals re-touch the same hot pages: scale FPIs down with
        # interval growth beyond 60 s (repeat touches are not re-imaged).
        damp = min(1.0, 60.0 / max(interval, 1.0)) if interval > 60 else 1.0
        wal_rate = base_wal + fpi_rate * damp
    fpi_wal = wal_rate - base_wal
    ckpt_write = (
        dirty_pages_s
        * interval
        * pgc.PAGE_SIZE
        / max(interval * pgc.DEFAULT_CHECKPOINT_COMPLETION_TARGET, 1.0)
    ) / 1e6

    # --- Autovacuum ----------------------------------------------------------
    heavy_running = 8.0
    jobs_updates_per_day = (
        (sc.ingest_docs_per_day + sc.reannotate_docs_per_day)
        * reg.value("corpus.jobs_per_document")
        * 3
        + heavy_running * 86_400.0 / 15.0  # heartbeat UPDATE per 15 s per running job
    )
    by_name = {t.name: t for t in sizes.tables}
    out: list[TableVacuum] = []
    total_points_per_day = 0.0
    for spec in tables:
        dead_day = _churn_rows_per_day(spec, sc, jobs_updates_per_day)
        if dead_day <= 0:
            continue
        size = by_name[spec.name]
        scale = pgc.DEFAULT_AV_VACUUM_SCALE
        thresh = float(pgc.DEFAULT_AV_VACUUM_THRESHOLD)
        if spec.autovacuum_override:
            scale, thresh = 0.02, 1000.0  # db/migrations/034:7-33
        trigger = thresh + scale * spec.row_count
        cycles = dead_day / trigger if trigger else 0.0
        heap_pages = size.heap_bytes / pgc.PAGE_SIZE
        index_pages = size.total_index_bytes / pgc.PAGE_SIZE
        rows_per_page = max(spec.row_count / max(heap_pages, 1.0), 1.0)
        dirty = min(trigger / rows_per_page + index_pages * 0.05, heap_pages + index_pages)
        # PG13+ visibility map lets vacuum skip all-visible heap pages; assume
        # the reannotation working set touches a bounded heap fraction.
        touched_heap = min(heap_pages, trigger / rows_per_page * 3.0)
        points = _vacuum_cost_points(touched_heap, index_pages, dirty)
        hours = points / AV_POINTS_PER_S / 3600.0
        if cycles > 0:
            total_points_per_day += points * cycles
        steady_dead = min(1.0, (trigger + dead_day * hours / 24.0) / max(spec.row_count, 1.0))
        out.append(
            TableVacuum(
                name=spec.name,
                dead_rows_per_day=dead_day,
                trigger_rows=trigger,
                cycles_per_day=cycles,
                vacuum_hours_per_cycle=hours,
                vacuum_hours_per_day=hours * cycles,
                steady_dead_fraction=steady_dead,
            )
        )
    demand = total_points_per_day / (AV_POINTS_PER_S * 86_400.0)
    return MaintenanceResult(
        scenario=sc.name,
        base_wal_mb_s=base_wal / 1e6,
        fpi_wal_mb_s=fpi_wal / 1e6,
        total_wal_mb_s=wal_rate / 1e6,
        wal_gb_per_day=wal_rate * 86_400.0 / 1e9,
        checkpoint_interval_s=interval,
        checkpoint_forced_by_size=forced,
        checkpoint_write_mb_s=ckpt_write,
        tables=sorted(out, key=lambda t: -t.vacuum_hours_per_day),
        autovacuum_demand_fraction=demand,
        autovacuum_keeping_up=demand < 1.0,
        replication_note=(
            "No streaming replica is deployed (repo fact: no standby in any compose "
            "file; provider PITR is the recovery path — docs/production-setup.md:455-457). "
            f"A standby would need to absorb {wal_rate / 1e6:.1f} MB/s of WAL; "
            "app-native backups COPY every table daily (application/backup.py), adding "
            "a full-corpus sequential read inside one long transaction."
        ),
    )
