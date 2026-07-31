-- doc-ray: schema SemVer 1.57.2 — the rollup driver can call its helpers again.
--
-- Migration 159 grants EXECUTE on the three per-document rollup helpers to
-- `docray_batch`, the role that owns their only caller and therefore the role
-- their caller's body runs as. Privileges on a function are part of the schema
-- contract, so the repair carries a version even though no table, column, policy
-- or function body changes.
--
-- PATCH, not minor: nothing gains or loses a capability. The delta-based rollup
-- path shipped in 154 was always specified to maintain `lexeme_stats`,
-- `entity_frequency_stats` and the `corpus_stats` totals derived from them; it
-- simply could not, and now does.
--
-- Keeps doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md and the
-- database in lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.57.2')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('160_schema_version_1_57_2.sql')
ON CONFLICT (filename) DO NOTHING;
