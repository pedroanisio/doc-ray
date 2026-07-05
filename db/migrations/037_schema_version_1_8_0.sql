-- doc-ray: schema SemVer 1.8.0.
--
-- Advances the unified schema/package version for the evaluation and
-- corpus-drift artifacts introduced by 036_evaluation_drift.sql.

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.8.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename) VALUES ('037_schema_version_1_8_0.sql')
    ON CONFLICT (filename) DO NOTHING;
