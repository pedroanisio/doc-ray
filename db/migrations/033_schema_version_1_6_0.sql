-- doc-ray: schema SemVer 1.6.0.
--
-- Reconciles the API-role runtime grants and seed_suggestion RLS policies
-- introduced by 032_api_role_runtime_grants.sql.

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.6.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename) VALUES ('033_schema_version_1_6_0.sql')
    ON CONFLICT (filename) DO NOTHING;
