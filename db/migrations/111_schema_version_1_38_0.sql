-- doc-ray: schema SemVer 1.38.0 (organization corpus / document visibility; migration 110).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.38.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('111_schema_version_1_38_0.sql')
ON CONFLICT (filename) DO NOTHING;
