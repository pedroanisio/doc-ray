-- doc-ray: schema SemVer 1.34.0 (per-owner corpus isolation; migration 102).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.34.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('103_schema_version_1_34_0.sql')
ON CONFLICT (filename) DO NOTHING;
