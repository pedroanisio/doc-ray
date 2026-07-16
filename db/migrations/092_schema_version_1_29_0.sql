-- doc-ray: schema SemVer 1.29.0 (Prism Tier-2 vision embeddings — additive tables in 091).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.29.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('092_schema_version_1_29_0.sql')
ON CONFLICT (filename) DO NOTHING;
