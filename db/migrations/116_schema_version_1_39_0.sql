-- doc-ray: schema SemVer 1.39.0 (NER correction substrate; migration 115).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.39.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('116_schema_version_1_39_0.sql')
ON CONFLICT (filename) DO NOTHING;
