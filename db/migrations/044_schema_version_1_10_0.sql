-- doc-ray: schema SemVer 1.10.0.
--
-- Advances the unified schema/package version for jobs/backups RLS
-- (migration 043, BL-064).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.10.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename) VALUES ('044_schema_version_1_10_0.sql')
    ON CONFLICT (filename) DO NOTHING;
