-- doc-ray: schema SemVer 1.28.0 (Prism Tier-1 visual layer — additive tables in 089).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.28.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('090_schema_version_1_28_0.sql')
ON CONFLICT (filename) DO NOTHING;
