-- doc-ray: schema SemVer 1.33.0 (read-model token + entity-frequency rollups; migration 100).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.33.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('101_schema_version_1_33_0.sql')
ON CONFLICT (filename) DO NOTHING;
