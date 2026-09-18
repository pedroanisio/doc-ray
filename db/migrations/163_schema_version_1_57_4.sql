-- doc-ray: schema SemVer 1.57.4 — the per-document rollup helpers run as docray_batch.
--
-- Migration 162 assigns the three per-document rollup helpers from migration 154
-- to `docray_batch`, verifies the batch-owner/RLS invariant, and rebuilds every
-- tenant's rollups once. Function ownership decides which role a SECURITY
-- DEFINER body runs as, so it is part of the schema contract and the repair
-- carries a version even though no table, column, policy or function body
-- changes.
--
-- PATCH, not minor: nothing gains or loses a capability. The delta-based rollup
-- path was always specified to maintain `lexeme_stats`, `entity_frequency_stats`
-- and the `corpus_stats` totals derived from them; owned by a role that RLS
-- blinds, it could not, and now does.
--
-- Keeps doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md and the
-- database in lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.57.4')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('163_schema_version_1_57_4.sql')
ON CONFLICT (filename) DO NOTHING;
