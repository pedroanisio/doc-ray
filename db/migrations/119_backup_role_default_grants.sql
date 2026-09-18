-- doc-ray: the backup role must be able to read EVERY table, now and in future.
--
-- SYMPTOM: every backup job failed with
--   InsufficientPrivilege: permission denied for table "web_auth_sessions"
-- and the corpus had no recoverable snapshot for as long as it went unnoticed.
--
-- ROOT CAUSE. A backup COPYs every public base table, elevated to the trusted
-- writer `docray_batch`. Migration 106 granted `docray_batch` access with a
-- ONE-TIME `GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public` — a snapshot of
-- the tables that existed then. It also never set DEFAULT PRIVILEGES, so every
-- table created afterward (migration 112's `web_auth_sessions`, and any future
-- one) is invisible to `docray_batch`, and the whole backup aborts on it. This
-- is a latent trap that re-arms on the next `CREATE TABLE`.
--
-- FIX (two parts, both required):
--   1. Catch-up: re-grant over ALL current tables/sequences, covering the drift.
--   2. Durable: ALTER DEFAULT PRIVILEGES so tables/sequences created hereafter by
--      the migration role are auto-granted to docray_batch. No FOR ROLE clause,
--      so it binds current_user — `docray` in production, `docray_test` in the
--      test harness — which is exactly the role that runs migrations and creates
--      tables in each environment.
-- The application backup path is also hardened to fail loudly, naming any
-- unreadable table in a preflight instead of dying mid-COPY (see
-- application/backup.py and test_backup_grants.py).

GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public TO docray_batch;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO docray_batch;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES    TO docray_batch;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO docray_batch;

INSERT INTO schema_migrations (filename)
VALUES ('119_backup_role_default_grants.sql')
ON CONFLICT (filename) DO NOTHING;
