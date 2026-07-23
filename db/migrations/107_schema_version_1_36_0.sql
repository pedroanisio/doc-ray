-- doc-ray: schema SemVer 1.36.0 (FORCE ROW LEVEL SECURITY + batch writer; migration 106).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.36.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('107_schema_version_1_36_0.sql')
ON CONFLICT (filename) DO NOTHING;
