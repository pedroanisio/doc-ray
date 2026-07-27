-- doc-ray: recurring schedules for corpus-wide maintenance operations.
--
-- WHY THIS TABLE EXISTS. Corpus backfills (visual tiers 1-3, semantic sentence
-- embedding, PDF geometry re-extract) were reachable only as operator CLI
-- scripts under scripts/. That made every re-drive a shell task on the host, and
-- it made "is this corpus fully embedded?" a question nobody could answer from a
-- browser. The admin console needs the same three verbs it already offers for
-- documents and backups — see status, run, schedule — and a schedule needs
-- somewhere durable to live.
--
-- WHY A STRUCTURED CADENCE AND NOT A CRON STRING. A crontab expression is a
-- parser problem with silent failure modes: a subtly wrong field yields a
-- schedule that simply never fires. The cadence column stores the validated
-- structure produced by doc_ray.domain.operation_cadence (interval / daily /
-- weekly / monthly, UTC), so the console renders a form instead of asking a
-- human to hand-write a string, and next_run_at is computed by tested code.
--
-- WHY next_run_at IS MATERIALIZED. The scheduler's due-scan must be an index
-- range read, not a per-row cadence evaluation across every schedule. The column
-- is advanced in the same transaction that claims the row, which is what makes
-- concurrent schedulers safe (FOR UPDATE SKIP LOCKED, exactly like the jobs
-- queue): two schedulers cannot both fire one schedule, and a crash mid-run
-- rolls the claim back rather than losing the tick.
--
-- READ PATH: admin-only, and bounded (one row per schedule, no aggregation over
-- a fact table), so D-024 is satisfied by construction.

CREATE TABLE IF NOT EXISTS operation_schedule (
    id              ulid PRIMARY KEY,
    operation_key   text        NOT NULL,   -- doc_ray.domain.operations.OPERATION_KEYS
    cadence         jsonb       NOT NULL,   -- validated by domain/operation_cadence.py
    only_missing    boolean     NOT NULL DEFAULT true,
    priority        int         NOT NULL DEFAULT 500,  -- background lane; see BACKFILL_PRIORITY
    enabled         boolean     NOT NULL DEFAULT true,
    note            text,
    next_run_at     timestamptz NOT NULL,
    last_run_at     timestamptz,
    last_job_count  int,                    -- jobs enqueued by the most recent firing
    last_error      text,                   -- why the most recent firing failed, if it did
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    created_by      uuid REFERENCES principals(id) ON DELETE SET NULL,
    CONSTRAINT operation_schedule_priority_sane CHECK (priority >= 0),
    CONSTRAINT operation_schedule_job_count_sane
        CHECK (last_job_count IS NULL OR last_job_count >= 0),
    CONSTRAINT operation_schedule_key_nonempty CHECK (btrim(operation_key) <> '')
);

-- The scheduler's only hot query: due, enabled schedules in fire order. Partial
-- on `enabled` so a disabled schedule costs nothing to skip.
CREATE INDEX IF NOT EXISTS operation_schedule_due_idx
    ON operation_schedule (next_run_at, id)
    WHERE enabled;

COMMENT ON TABLE operation_schedule IS
    'Recurring corpus-maintenance operations. One row per schedule; the scheduler '
    'claims due rows with FOR UPDATE SKIP LOCKED and advances next_run_at in the '
    'same transaction.';
COMMENT ON COLUMN operation_schedule.cadence IS
    'Validated recurrence structure (kind + fields) from domain/operation_cadence.py, '
    'never a raw cron string.';
COMMENT ON COLUMN operation_schedule.next_run_at IS
    'Materialized next firing time (UTC). Advanced inside the claiming transaction so '
    'concurrent schedulers cannot double-fire one schedule.';

-- The API reads the catalog (admin-gated below); the trusted batch writer owns
-- every mutation, exactly as it does for the jobs queue (migration 113).
GRANT SELECT ON operation_schedule TO docray_client;
GRANT SELECT, INSERT, UPDATE, DELETE ON operation_schedule TO docray_batch;

-- Schedules are corpus-wide by nature: there is no per-document grant that would
-- make sense, so visibility is admin-only, like the backup catalog (043). RLS is
-- FORCED with no write policy: the scheduler and the API's enqueue path reach the
-- table as docray_batch through BYPASSRLS (106), and any other role — including
-- an owner connection that forgot to SET ROLE — sees nothing rather than
-- everything.
ALTER TABLE operation_schedule ENABLE ROW LEVEL SECURITY;
ALTER TABLE operation_schedule FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_read ON operation_schedule;
CREATE POLICY p_read ON operation_schedule
    FOR SELECT
    USING (docray_is_admin());

-- NO schema_version bump here, deliberately. This migration adds a table but
-- does not cut a release: 142/143 are an in-flight 1.52.0 landing from another
-- workstream, and a second marker racing it would put two versions in the same
-- lockstep chain (scripts/check_version_consistency.py reads the newest marker
-- and compares it to doc_ray.__version__, CHANGELOG.md, and
-- engineering_decisions.yml). Pair this file with a version-marker migration
-- when the release is cut, exactly as 140 was paired with 141 and 142 with 143.

INSERT INTO schema_migrations (filename)
VALUES ('144_operation_schedule.sql')
ON CONFLICT (filename) DO NOTHING;
