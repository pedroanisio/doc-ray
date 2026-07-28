-- doc-ray: schema SemVer 1.54.0 — readable-sentence candidate helper (BL-187).
--
-- Migration 148 adds docray_readable_sentence_ids, a SECURITY DEFINER helper
-- that restores index-backed sentence-text search. Additive: no table, column
-- or policy changes, and no existing signature altered. This marker keeps
-- doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md and the database
-- in lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.54.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('149_schema_version_1_54_0.sql')
ON CONFLICT (filename) DO NOTHING;
