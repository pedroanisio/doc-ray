-- doc-ray: schema SemVer 1.7.0.
--
-- Reconciles scheduled backup retention and annotation-table autovacuum tuning
-- introduced by 034_annotation_table_autovacuum.sql and the 1.7.0 runtime.

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.7.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename) VALUES ('035_schema_version_1_7_0.sql')
    ON CONFLICT (filename) DO NOTHING;
