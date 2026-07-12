-- doc-ray: schema SemVer 1.12.0.
--
-- Advances the unified schema/package version for document collections
-- introduced by migration 047 (BL-057).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.12.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename) VALUES ('048_schema_version_1_12_0.sql')
    ON CONFLICT (filename) DO NOTHING;
