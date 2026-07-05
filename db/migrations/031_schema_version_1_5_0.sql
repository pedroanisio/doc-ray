-- doc-ray: bump the schema's semantic version 1.4.0 -> 1.5.0.
--
-- Advances the queryable schema contract for migration 030:
--   030_principal_login_roles.sql -> 1.5.0
-- The principal LOGIN-role isolation model is an additive security feature:
-- it adds principals.db_role, docray_api, and helper functions while preserving
-- the trusted batch-writer path and existing corpus tables.

UPDATE schema_version
   SET version = '1.5.0', updated_at = now()
 WHERE singleton;

INSERT INTO schema_migrations (filename) VALUES ('031_schema_version_1_5_0.sql')
    ON CONFLICT (filename) DO NOTHING;
