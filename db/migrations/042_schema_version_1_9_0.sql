-- doc-ray: schema SemVer 1.9.0.
--
-- Advances the unified schema/package version for the entity_mention/chunk
-- surface denormalization introduced by migrations 038-041.

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.9.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename) VALUES ('042_schema_version_1_9_0.sql')
    ON CONFLICT (filename) DO NOTHING;
