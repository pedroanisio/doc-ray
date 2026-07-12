-- doc-ray: schema SemVer 1.22.0.

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.22.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('073_schema_version_1_22_0.sql')
ON CONFLICT (filename) DO NOTHING;
