-- doc-ray: schema SemVer 1.56.0 — maintained term-frequency denominator (BL-204).
--
-- Migration 152 adds corpus_stats.total_lexeme_uses and redefines
-- docray_refresh_corpus_stats to maintain it. Additive: one nullable column and
-- a function body change; no policy, table or signature alterations. This marker
-- keeps doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md and the
-- database in lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.56.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('153_schema_version_1_56_0.sql')
ON CONFLICT (filename) DO NOTHING;
