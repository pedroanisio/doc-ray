-- doc-ray: deploy-safe drain release for the job queue.
--
-- A worker that receives SIGTERM keeps running its in-flight job (graceful
-- drain) and stamps that job here from the heartbeat thread. If the process
-- is later SIGKILLed before the handler finishes (stop_grace_period expiry
-- on deploy), the reaper sees the stamp and requeues the job WITHOUT
-- spending an attempt: a deploy-kill is an operational event, not a job
-- failure, so it must never exhaust max_attempts. Cleared on every claim.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS drain_requested_at timestamptz;

COMMENT ON COLUMN jobs.drain_requested_at IS
    'Set while running when the claim-holding worker was asked to shut down; '
    'a stale-heartbeat reap of such a job requeues it without burning an attempt.';

INSERT INTO schema_migrations (filename) VALUES ('072_jobs_drain_requested_at.sql')
    ON CONFLICT (filename) DO NOTHING;
