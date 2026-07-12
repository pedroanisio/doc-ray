-- doc-ray: schema SemVer 1.14.0.
--
-- Advances the unified schema/package version for document lifecycle
-- state and reversible soft delete/restore introduced by migration 051
-- (BL-059).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.14.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename) VALUES ('052_schema_version_1_14_0.sql')
    ON CONFLICT (filename) DO NOTHING;
