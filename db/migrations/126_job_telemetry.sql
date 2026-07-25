-- doc-ray: append-only execution telemetry for cost attribution (schema 1.44.0).
--
-- WHY A SEPARATE TABLE AND NOT COLUMNS ON `jobs`
--
-- `jobs` holds the CURRENT state of a unit of work. It is overwritten on every
-- retry: `started_at`, `finished_at` and `error` describe the *last* attempt
-- only. Cost accounting needs the opposite — every attempt that ever consumed a
-- machine, including the ones that failed.
--
-- This is not hypothetical. On 2026-07-25 a GPU-memory exhaustion caused 41
-- annotate attempts to fail; each burned real GPU seconds on a billed host, and
-- each was then erased from `jobs` by the successful retry that followed. The
-- money was spent and left no trace. An append-only fact row per execution is
-- the only way a cost model can see it.
--
-- It also records WHERE work ran. With GPU-only worker hosts joining and leaving
-- the pool (see docker-compose.gpuworker.yml), `jobs.locked_by` alone cannot
-- attribute hours to a billed machine: it carries a process identity, not a host
-- or a device. Cost per host-hour requires both.
--
-- SHAPE
--   * One row per finished execution attempt. Never updated, never deleted by
--     the application — a fact table, not state.
--   * `work_units` is the size driver for the kind (sentences for annotate and
--     the text stages, images for the visual tiers). Duration alone cannot
--     separate "a big document" from "a slow host"; this column can.
--   * `job_id` is deliberately NOT a foreign key. Telemetry must outlive the
--     work it describes — a purged or archived job must not take its cost
--     history with it, and an FK would either block the purge or cascade the
--     evidence away.
--
-- READ PATH: none. This table is written by workers and read offline by
-- scripts/cost_telemetry.py. It is never joined on a user-facing read path, so
-- D-024 (no live fact-table aggregates on read paths) is satisfied by
-- construction — no client role is granted SELECT.

CREATE TABLE IF NOT EXISTS job_telemetry (
    id              bigserial PRIMARY KEY,
    job_id          text        NOT NULL,
    kind            text        NOT NULL,
    lane            text        NOT NULL CHECK (lane IN ('gpu', 'cpu')),
    outcome         text        NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'canceled')),
    attempt         integer     NOT NULL,
    document_id     uuid,
    worker_id       text        NOT NULL,
    host            text        NOT NULL,
    device          text        NOT NULL,
    work_units      bigint,
    duration_ms     bigint      NOT NULL CHECK (duration_ms >= 0),
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz NOT NULL DEFAULT now()
);

-- Cost queries slice by time first, then by host or kind; a plain btree on the
-- finish timestamp serves the window scan, and the composite serves per-host
-- attribution without a second pass over the window.
CREATE INDEX IF NOT EXISTS job_telemetry_finished_at_idx
    ON job_telemetry (finished_at DESC);
CREATE INDEX IF NOT EXISTS job_telemetry_host_kind_idx
    ON job_telemetry (host, kind, finished_at DESC);

-- The trusted batch writer (workers, CLIs) inserts; nothing else touches it.
-- docray_client is deliberately absent: telemetry is operator data, not corpus
-- data, and no user-facing surface reads it.
GRANT SELECT, INSERT ON job_telemetry TO docray_batch;
GRANT USAGE, SELECT ON SEQUENCE job_telemetry_id_seq TO docray_batch;

-- RLS is enabled and FORCED with no permissive policy: the batch writer reaches
-- it through BYPASSRLS (migration 106), and any other role — including a table
-- owner that forgot to SET ROLE — reads nothing rather than everything.
ALTER TABLE job_telemetry ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_telemetry FORCE ROW LEVEL SECURITY;

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.44.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('126_job_telemetry.sql')
ON CONFLICT (filename) DO NOTHING;
