-- doc-ray: schema SemVer 1.31.0 (corpus stat-band rollups — tables in 095).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.31.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('096_schema_version_1_31_0.sql')
ON CONFLICT (filename) DO NOTHING;
