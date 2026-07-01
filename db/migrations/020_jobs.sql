-- doc-ray: async job queue (engineering decision D-007).
-- One row per unit of background work (ingest, reannotate, ...). Workers claim
-- rows with SELECT ... FOR UPDATE SKIP LOCKED (see adapters/queue_postgres.py).
--
-- status is a CLOSED lifecycle -> text + CHECK (transaction-safe to evolve; a PG
-- enum's ADD VALUE is not). kind is OPEN -> plain text, validated by the worker's
-- dispatch table so an unknown kind fails the job, not the INSERT.
-- (016-019 were taken by fts / schema-fixes / rbac / schema-version.)

CREATE TABLE IF NOT EXISTS jobs (
    id            ulid PRIMARY KEY,                       -- time-sortable, like document_provenance
    kind          text NOT NULL,                          -- 'ingest' | 'reannotate' | 'delete' | ...
    status        text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')),
    priority      int  NOT NULL DEFAULT 100,              -- lower = sooner
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,     -- kind-specific input
    result        jsonb,                                  -- kind-specific success output
    error         text,                                   -- last failure reason
    attempts      int  NOT NULL DEFAULT 0,
    max_attempts  int  NOT NULL DEFAULT 3,
    document_id   uuid REFERENCES documents(id) ON DELETE CASCADE,  -- nullable: set once known
    created_at    timestamptz NOT NULL DEFAULT now(),
    run_after     timestamptz NOT NULL DEFAULT now(),     -- backoff / scheduled start
    started_at    timestamptz,
    finished_at   timestamptz,
    heartbeat_at  timestamptz,                            -- liveness while running
    locked_by     text,                                   -- worker id holding the claim
    CHECK (attempts <= max_attempts),
    CHECK (finished_at IS NULL OR started_at IS NOT NULL)
);

-- The claim scan: only eligible queued rows, oldest-eligible first, cheaply.
CREATE INDEX IF NOT EXISTS idx_jobs_claimable
    ON jobs (priority, run_after, id)
    WHERE status = 'queued';

-- Reaper scan: running jobs whose heartbeat went stale.
CREATE INDEX IF NOT EXISTS idx_jobs_running_heartbeat
    ON jobs (heartbeat_at)
    WHERE status = 'running';

-- job(id) / jobs-for-a-document lookups.
CREATE INDEX IF NOT EXISTS idx_jobs_document ON jobs (document_id, id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status, id);

INSERT INTO schema_migrations (filename) VALUES ('020_jobs.sql')
    ON CONFLICT (filename) DO NOTHING;
