-- doc-ray: schema SemVer 1.57.0 — incremental tenant rollups.
--
-- Migration 154 adds the two per-document snapshot tables, replaces the
-- full-rebuild refresh path with a delta path, and moves corpus_stats off its
-- count(DISTINCT) over lexeme_occurrences. New tables and redefined function
-- bodies only — every existing signature is preserved, so a rolling deploy
-- keeps working against either revision. This marker keeps
-- doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md and the database
-- in lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.57.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('155_schema_version_1_57_0.sql')
ON CONFLICT (filename) DO NOTHING;
