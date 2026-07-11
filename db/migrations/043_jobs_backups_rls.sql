-- doc-ray: Row-Level Security for jobs and backups (BL-064).
--
-- jobs (020_jobs.sql) and backups (026_backups.sql) were granted to
-- docray_client/docray_api directly with no RLS attached, and every mutation
-- resolver (entrypoints/api/mutation.py) checked only a global "write" scope,
-- never the caller's document_acl grant for the specific document_id being
-- mutated. Net effect: any token with global write scope could enqueue
-- reannotate/delete/canonicalize/restore against ANY document in the corpus,
-- not just the ones it was granted. This migration closes the schema-level
-- half of the gap; mutation.py closes the resolver-level half (an explicit
-- check before enqueuing, so callers get a clean, expected error instead of a
-- raw RLS policy violation).

-- 018_rbac.sql granted EXECUTE on docray_readable_documents()/
-- docray_writable_documents() to docray_client, but not on docray_is_admin()
-- itself — nothing needed to call it directly before now. These policies do,
-- so extend the grant (the function only returns a boolean; it leaks nothing
-- callers couldn't already infer from whether their own writes succeed).
GRANT EXECUTE ON FUNCTION docray_is_admin() TO docray_client;

ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_read ON jobs;
DROP POLICY IF EXISTS p_ins ON jobs;

-- Corpus-wide jobs (document_id IS NULL: backup/restore) are admin-only;
-- document-scoped jobs follow the caller's existing read/write document
-- grants (the same docray_readable_documents()/docray_writable_documents()
-- helpers documents/spans/etc. already use, so this is one consistent model,
-- not a second one).
CREATE POLICY p_read ON jobs
    FOR SELECT
    USING (
        (document_id IS NULL AND docray_is_admin())
        OR document_id IN (SELECT * FROM docray_readable_documents())
    );

CREATE POLICY p_ins ON jobs
    FOR INSERT
    WITH CHECK (
        (document_id IS NULL AND docray_is_admin())
        OR document_id IN (SELECT * FROM docray_writable_documents())
    );

-- Backups are corpus-wide by nature (one archive covers every document), so
-- there is no per-document grant that makes sense here — only admins may see
-- the catalog. (The worker that actually writes rows here runs on the
-- superuser connection and bypasses RLS by design, per D-010.)
ALTER TABLE backups ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_read ON backups;

CREATE POLICY p_read ON backups
    FOR SELECT
    USING (docray_is_admin());

INSERT INTO schema_migrations (filename) VALUES ('043_jobs_backups_rls.sql')
    ON CONFLICT (filename) DO NOTHING;
