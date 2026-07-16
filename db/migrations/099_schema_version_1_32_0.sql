-- doc-ray: schema SemVer 1.32.0 (canonical client grant + reader-page rollups; 097-098).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.32.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('099_schema_version_1_32_0.sql')
ON CONFLICT (filename) DO NOTHING;
