-- doc-ray: bump the schema's semantic version 1.3.0 -> 1.4.0.
--
-- Advances the queryable schema contract (schema_version / docray_schema_version())
-- to cover the first additive change since the 1.3.0 baseline (migration 024):
--   026_backups.sql -> 1.4.0  (corpus-backup archive catalogue)
-- A new table only = backward-compatible = MINOR bump (1.3.0 -> 1.4.0). 1.4.0 is
-- also the project's unified global version (doc_ray.__version__ / pyproject); the
-- two are kept in lockstep by scripts/check_version_consistency.py.
--
-- Backup archives capture docray_schema_version() at creation and restore refuses a
-- mismatch, so archives taken at 1.3.0 do not restore into a 1.4.0 database — the
-- schema they were dumped against no longer matches.

UPDATE schema_version
   SET version = '1.4.0', updated_at = now()
 WHERE singleton;

INSERT INTO schema_migrations (filename) VALUES ('029_schema_version_1_4_0.sql')
    ON CONFLICT (filename) DO NOTHING;
