-- doc-ray: schema SemVer 1.37.0 (owner-driven document sharing; migration 108).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.37.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('109_schema_version_1_37_0.sql')
ON CONFLICT (filename) DO NOTHING;
