-- doc-ray: schema SemVer 1.27.0.

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.27.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('086_schema_version_1_27_0.sql')
ON CONFLICT (filename) DO NOTHING;
