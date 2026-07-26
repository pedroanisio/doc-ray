-- doc-ray: schema SemVer 1.47.0 — allusion edges (SLP Block E, #9).
--
-- Migration 131 adds the additive `entity_allusion` curation table (typed cross-lens
-- links that never assert identity). An additive table is a MINOR bump under doc-ray's
-- one-global-version contract; this marker keeps doc_ray.__version__,
-- engineering_decisions.yml, CHANGELOG.md, and the database in lockstep
-- (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.47.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('132_schema_version_1_47_0.sql')
ON CONFLICT (filename) DO NOTHING;
