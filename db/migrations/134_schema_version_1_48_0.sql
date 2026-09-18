-- doc-ray: schema SemVer 1.48.0 — hyper-context lens rollups (issue #10).
--
-- Migration 133 replaces the thin lens_entity_stats with the richer lens_entity_rollup
-- and adds lens_entity_relation (co-occurrence relationship discovery). A schema change
-- is a version bump under doc-ray's one-global-version contract; this marker keeps
-- doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md, and the database in
-- lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.48.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('134_schema_version_1_48_0.sql')
ON CONFLICT (filename) DO NOTHING;
