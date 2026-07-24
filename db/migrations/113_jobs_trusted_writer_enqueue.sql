-- doc-ray: the jobs queue is trusted infrastructure, not a user-writable table (BL-141).
--
-- SYMPTOM (production): every non-admin member upload failed with
--   new row violates row-level security policy for table "jobs"
--
-- ROOT CAUSE. Migration 043 gave `jobs` an INSERT policy `p_ins` whose author
-- pictured a document-less job (document_id IS NULL) as meaning "corpus-wide
-- backup/restore", and gated it behind docray_is_admin(). But an *upload's*
-- ingest job is also document-less at enqueue time — the document does not exist
-- until the worker creates it from the bytes — and, under the ownership model
-- (102/104), it is enqueued by a non-admin `member`. So the policy blocked the
-- product's core member capability. It could never have been expressed as a
-- document-keyed check either: a not-yet-existent row's id is never in
-- docray_writable_documents() (the same wall migration 106 documents).
--
-- FIX (how mature systems treat an async queue). Authorization happens once, at
-- the API boundary (mutation_guards: write scope, per-document ACL, or admin);
-- the enqueue then runs as the trusted writer `docray_batch` (adapters/
-- queue_postgres.enqueue_async elevates the RLS-bound request connection with
-- SET LOCAL ROLE). The queue table therefore needs NO user-facing INSERT policy:
-- with forced RLS (106) that denies every non-bypass role, which is exactly the
-- intent — only the trusted writer enqueues. RLS on `jobs` now governs READ
-- visibility only.
--
-- READ visibility gains an owner axis. A job records the principal it acts for
-- (`enqueued_by`), so a member can see the jobs they created — including their
-- document-less ingest job, which no document-scoped rule could show them —
-- without seeing anyone else's. Worker/CLI/system jobs leave it NULL.

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS enqueued_by uuid REFERENCES principals(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS jobs_enqueued_by_idx
    ON jobs (enqueued_by)
    WHERE enqueued_by IS NOT NULL;

COMMENT ON COLUMN jobs.enqueued_by IS
    'Principal the job acts for (the API request principal). NULL = system/worker '
    'job with no request principal. Drives the jobs read policy''s owner branch.';

-- The write gate moves to the trusted writer: drop the user-facing INSERT policy
-- entirely. Forced RLS + no INSERT policy = no user role may enqueue directly;
-- docray_batch (BYPASSRLS) is the only writer, and the API authorizes upstream.
DROP POLICY IF EXISTS p_ins ON jobs;

-- Read visibility: admin sees all; otherwise a caller sees jobs they enqueued or
-- jobs on a document they can read. (Replaces 043's read policy, adding the
-- enqueued_by owner branch so a member can track their own document-less ingest.)
DROP POLICY IF EXISTS p_read ON jobs;
CREATE POLICY p_read ON jobs
    FOR SELECT
    USING (
        docray_is_admin()
        OR enqueued_by = docray_current_principal()
        OR document_id IN (SELECT * FROM docray_readable_documents())
    );

INSERT INTO schema_migrations (filename) VALUES ('113_jobs_trusted_writer_enqueue.sql')
    ON CONFLICT (filename) DO NOTHING;
