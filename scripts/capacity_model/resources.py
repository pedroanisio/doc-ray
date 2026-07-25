"""Hardware and PostgreSQL configuration profiles, from repo facts + stock defaults.

Repo facts (each cited on its assumption):

* The repo configures **no** PostgreSQL GUC anywhere — the compose postgres
  service starts the stock ``pgvector/pgvector:pg16`` entrypoint with no
  ``-c`` flags and no config mount (``docker-compose.yml:6-16``), so stock
  defaults from :mod:`capacity_model.pg_constants` apply unless the managed
  provider overrides them.
* The documented production database is a DigitalOcean managed cluster,
  4 vCPU / 16 GB, ``max_connections = 400`` verified live
  (``docs/proposals/prism-visual-layer.md:182,185``); the 8 GB plan was
  measured with provider-set ``shared_buffers ≈ 2 GB``
  (``docs/production-setup.md:68``), i.e. the provider sets ~25% of RAM.
* Connection topology (``docker-compose.prod.yml``, ``config/env.registry.yaml``,
  ``src/doc_ray/entrypoints/worker.py:112-122``): API = 2 uvicorn workers ×
  psycopg_pool(max 10); webapp = 1 gunicorn worker × 8 gthreads, **unpooled**
  one connection per request (``webapp/app.py:316``); MCP holds no pool
  (fronts GraphQL); each queue worker holds 2 long-lived connections.
* Every pooled API connection carries ``statement_timeout = 15 s``
  (``src/doc_ray/entrypoints/api/app.py:171``).
"""

from __future__ import annotations

from dataclasses import dataclass

from capacity_model import pg_constants as pgc
from capacity_model.assumptions import AssumptionRegistry, Epistemic, Source


@dataclass(frozen=True)
class HardwareProfile:
    """One candidate database host."""

    name: str
    description: str
    cores: int
    ram_bytes: float
    disk_read_iops: float
    disk_write_iops: float
    disk_seq_mbps: float
    disk_random_latency_s: float
    disk_capacity_bytes: float


@dataclass(frozen=True)
class PgConfig:
    """Effective PostgreSQL settings for a profile (configured or stock default)."""

    max_connections: int
    shared_buffers_bytes: float
    effective_cache_bytes: float
    work_mem_bytes: float
    maintenance_work_mem_bytes: float
    checkpoint_timeout_s: float
    max_wal_size_bytes: float
    statement_timeout_s: float


@dataclass(frozen=True)
class ConnectionTopology:
    """Long-lived backend demand from the deployed process tree."""

    api_backends_max: int
    webapp_backends_max: int
    heavy_workers: int
    fast_workers: int
    per_worker_connections: int
    overhead_connections: int

    def worker_backends(self) -> int:
        """Connections pinned by the two queue-worker lanes."""
        return (self.heavy_workers + self.fast_workers) * self.per_worker_connections

    def total_max_backends(self) -> int:
        """Worst-case simultaneous backends the app tier can open."""
        return (
            self.api_backends_max
            + self.webapp_backends_max
            + self.worker_backends()
            + self.overhead_connections
        )


def documented_production_hardware(reg: AssumptionRegistry) -> HardwareProfile:
    """The 4 vCPU / 16 GB managed cluster the repo documents as current prod."""
    reg.define(
        "hw.prod.cores",
        4,
        "vCPU",
        Source.REPO,
        "docs/proposals/prism-visual-layer.md:182",
        "Documented live cluster size (resized from the 2 vCPU/8 GB plan).",
    )
    reg.define(
        "hw.prod.ram_gb",
        16,
        "GiB",
        Source.REPO,
        "docs/proposals/prism-visual-layer.md:182",
        "Documented live cluster RAM.",
    )
    disk_iops = reg.define(
        "hw.disk.read_iops",
        15000,
        "IOPS",
        Source.ASSUMED,
        "conservative engineering assumption for managed NVMe-backed volumes",
        "DO managed Postgres storage is SSD; provider publishes no IOPS figure, "
        "so a mid-range NVMe cloud-volume number is assumed, not measured.",
        epistemic=Epistemic.EMPIRICAL,
        sensitivity_key="disk_read_iops",
    )
    write_iops = reg.define(
        "hw.disk.write_iops",
        10000,
        "IOPS",
        Source.ASSUMED,
        "conservative engineering assumption for managed NVMe-backed volumes",
        "Write path shares the same unspecified device; assumed below read IOPS.",
        epistemic=Epistemic.EMPIRICAL,
    )
    latency = reg.define(
        "hw.disk.random_read_latency_s",
        0.0005,
        "s",
        Source.ASSUMED,
        "conservative engineering assumption (0.5 ms cloud SSD random read)",
        "Cloud SSD random reads are typically 0.1–1 ms; midpoint taken.",
        epistemic=Epistemic.EMPIRICAL,
        sensitivity_key="disk_latency",
    )
    return HardwareProfile(
        name="prod-16gb",
        description="Documented DO managed cluster: 4 vCPU / 16 GB / ~120 GiB provisioned",
        cores=int(reg.value("hw.prod.cores")),
        ram_bytes=reg.value("hw.prod.ram_gb") * 1024**3,
        disk_read_iops=disk_iops.value,
        disk_write_iops=write_iops.value,
        disk_seq_mbps=800.0,
        disk_random_latency_s=latency.value,
        disk_capacity_bytes=120 * 1024**3,
    )


def scaled_hardware(
    reg: AssumptionRegistry, cores: int, ram_gb: int, disk_tb: float
) -> HardwareProfile:
    """A candidate scaled-up host for the 1M-document target (model output, not repo fact)."""
    reg.define(
        "hw.scaled.cores",
        cores,
        "vCPU",
        Source.ASSUMED,
        "candidate sizing explored by this model",
        "Not a repo fact: a host class the model evaluates against the target workload.",
        epistemic=Epistemic.EMPIRICAL,
    )
    return HardwareProfile(
        name=f"scaled-{ram_gb}gb",
        description=f"Candidate scaled host: {cores} vCPU / {ram_gb} GiB / {disk_tb} TB NVMe",
        cores=cores,
        ram_bytes=ram_gb * 1024**3,
        disk_read_iops=80000.0,
        disk_write_iops=50000.0,
        disk_seq_mbps=2000.0,
        disk_random_latency_s=0.0002,
        disk_capacity_bytes=disk_tb * 1024**4,
    )


def pg_config_for(reg: AssumptionRegistry, hw: HardwareProfile) -> PgConfig:
    """Effective GUCs: provider pattern (25% shared_buffers) + stock defaults.

    The repo sets nothing server-side, so everything except the two
    provider-published values falls back to the stock defaults in
    :mod:`capacity_model.pg_constants` — reported as such.
    """
    reg.define(
        "pg.max_connections",
        400,
        "connections",
        Source.REPO,
        "docs/proposals/prism-visual-layer.md:185 (verified live on the 16 GB plan)",
        "Managed-provider setting; stock default would be 100.",
    )
    reg.define(
        "pg.shared_buffers_fraction",
        0.25,
        "fraction of RAM",
        Source.DERIVED,
        "docs/production-setup.md:68 (2 GB observed on the 8 GB plan → 25%)",
        "Provider sets shared_buffers ≈ 25% of instance RAM.",
    )
    shared = hw.ram_bytes * reg.value("pg.shared_buffers_fraction")
    reg.define(
        "pg.statement_timeout_s",
        15.0,
        "s",
        Source.REPO,
        "src/doc_ray/entrypoints/api/app.py:171 + config/env.registry.yaml:341-347",
        "Application injects statement_timeout=15000ms on every pooled API connection; "
        "this is the hard per-query failure threshold on the read path.",
    )
    return PgConfig(
        max_connections=int(reg.value("pg.max_connections")),
        shared_buffers_bytes=shared,
        effective_cache_bytes=hw.ram_bytes * 0.75,
        work_mem_bytes=float(pgc.DEFAULT_WORK_MEM_BYTES),
        maintenance_work_mem_bytes=float(pgc.DEFAULT_MAINTENANCE_WORK_MEM_BYTES),
        checkpoint_timeout_s=pgc.DEFAULT_CHECKPOINT_TIMEOUT_S,
        max_wal_size_bytes=float(pgc.DEFAULT_MAX_WAL_SIZE_BYTES),
        statement_timeout_s=reg.value("pg.statement_timeout_s"),
    )


def deployed_topology(reg: AssumptionRegistry) -> ConnectionTopology:
    """Backend demand from the documented prod process tree, scaled for 10k users."""
    api_workers = reg.define(
        "conn.api_workers",
        8,
        "processes",
        Source.ASSUMED,
        "docker-compose.prod.yml:64-66 defaults DOCRAY_API_WORKERS=2; scaled to 8 for 10k users",
        "The compose default (2) serves the current ~single-tenant corpus; a 10k-user "
        "deployment must raise it. 8 workers × pool 10 = 80 API backends.",
        epistemic=Epistemic.EMPIRICAL,
        sensitivity_key="api_workers",
    )
    pool_max = reg.define(
        "conn.api_pool_max",
        10,
        "connections/worker",
        Source.REPO,
        "config/env.registry.yaml:323-328 (DOCRAY_API_POOL_MAX_SIZE=10)",
        "psycopg_pool AsyncConnectionPool max_size; no overflow concept exists.",
    )
    webapp_threads = reg.define(
        "conn.webapp_threads",
        8,
        "threads",
        Source.REPO,
        "docker-compose.prod.yml:105-109 (gunicorn --workers 1 --threads 8, gthread)",
        "Webapp opens one UNPOOLED psycopg connection per request (webapp/app.py:316); "
        "8 threads bound worst-case simultaneous webapp backends per worker.",
    )
    webapp_workers = reg.define(
        "conn.webapp_workers",
        4,
        "processes",
        Source.ASSUMED,
        "docker-compose.prod.yml:98-104 pins 1 worker (in-memory sessions); the Postgres "
        "session store (docker-compose.yml:225) unblocks scaling — 4 assumed for 10k users",
        "At 10k users one 8-thread worker saturates; scaling requires the already-shipped "
        "DB session store. Connection cost is workers × threads because requests are unpooled.",
        epistemic=Epistemic.EMPIRICAL,
        sensitivity_key="webapp_workers",
    )
    heavy = reg.define(
        "conn.heavy_workers",
        8,
        "processes",
        Source.ASSUMED,
        "docker-compose.yml:338-372 (default 3; H200 guidance 4-8, gpu-batch-processing.md:81-89)",
        "Heavy annotation lane sized to the documented H200 starting range.",
        epistemic=Epistemic.EMPIRICAL,
        sensitivity_key="heavy_workers",
    )
    fast = reg.define(
        "conn.fast_workers",
        2,
        "processes",
        Source.ASSUMED,
        "docker-compose.yml:374-404 ships 1 worker-fast; 2 assumed for user-facing "
        "ingest at 10k users",
        "The fast lane handles user-visible ingest/metadata jobs and must not queue behind books.",
        epistemic=Epistemic.EMPIRICAL,
    )
    reg.define(
        "conn.per_worker",
        2,
        "connections/worker",
        Source.REPO,
        "src/doc_ray/entrypoints/worker.py:112-122 (claim/dispatch + heartbeat)",
        "Each worker process pins exactly two long-lived backends plus a short janitor tick.",
    )
    return ConnectionTopology(
        api_backends_max=int(api_workers.value * pool_max.value),
        webapp_backends_max=int(webapp_workers.value * webapp_threads.value),
        heavy_workers=int(heavy.value),
        fast_workers=int(fast.value),
        per_worker_connections=int(reg.value("conn.per_worker")),
        overhead_connections=6,
    )
