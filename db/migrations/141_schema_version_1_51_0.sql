-- doc-ray: schema SemVer 1.51.0 — durable exact-surface NER corrections.
--
-- Migration 140 extends the Block-A correction log with a second, additive
-- keying mode. This schema capability is a MINOR release and stays in lockstep
-- with the package, changelog, release notes, and engineering decisions.

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.51.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('141_schema_version_1_51_0.sql')
ON CONFLICT (filename) DO NOTHING;
