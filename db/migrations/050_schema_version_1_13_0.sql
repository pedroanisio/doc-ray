-- doc-ray: schema SemVer 1.13.0.
--
-- Advances the unified schema/package version for span page geometry
-- introduced by migration 049 (BL-077).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.13.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename) VALUES ('050_schema_version_1_13_0.sql')
    ON CONFLICT (filename) DO NOTHING;
