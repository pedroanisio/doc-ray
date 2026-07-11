-- doc-ray: schema SemVer 1.11.0.
--
-- Advances the unified schema/package version for the editable document
-- metadata (description column) and change-history table introduced by
-- migration 045 (BL-058).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.11.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename) VALUES ('046_schema_version_1_11_0.sql')
    ON CONFLICT (filename) DO NOTHING;
