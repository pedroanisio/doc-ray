-- doc-ray: schema SemVer 1.40.0.
--   * migration 118 — document_metadata_history admits `authors`
--   * migration 119 — docray_batch default-privilege grants for backups

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.40.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('120_schema_version_1_40_0.sql')
ON CONFLICT (filename) DO NOTHING;
