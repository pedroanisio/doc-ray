-- doc-ray: allow admin API principals to cancel queued/running jobs (BL-102).

GRANT UPDATE ON jobs TO docray_client;

ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_upd_cancel ON jobs;
CREATE POLICY p_upd_cancel ON jobs
  FOR UPDATE
  USING (docray_is_admin())
  WITH CHECK (docray_is_admin() AND status = 'canceled');

INSERT INTO schema_migrations (filename)
VALUES ('057_job_cancel_update_policy.sql')
ON CONFLICT (filename) DO NOTHING;
