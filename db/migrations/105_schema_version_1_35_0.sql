-- doc-ray: schema SemVer 1.35.0 (the `member` role; migration 104).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.35.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('105_schema_version_1_35_0.sql')
ON CONFLICT (filename) DO NOTHING;
