-- doc-ray: schema SemVer 1.30.0 (Prism Tier-3 grounded captions + review — tables in 093).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.30.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('094_schema_version_1_30_0.sql')
ON CONFLICT (filename) DO NOTHING;
