-- doc-ray: schema SemVer 1.23.0.

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.23.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('075_schema_version_1_23_0.sql')
ON CONFLICT (filename) DO NOTHING;
