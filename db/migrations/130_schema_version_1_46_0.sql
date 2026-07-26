-- doc-ray: schema SemVer 1.46.0 — Projection Engine (SLP Block D, #8).
--
-- Migration 129 adds four additive derived tables (entity_projection,
-- lens_local_entity, entity_projection_review, lens_entity_stats) and one additive
-- column (document_collections.projects). Additive schema is a MINOR bump under
-- doc-ray's one-global-version contract; this marker keeps doc_ray.__version__,
-- engineering_decisions.yml, CHANGELOG.md, and the database in lockstep
-- (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.46.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('130_schema_version_1_46_0.sql')
ON CONFLICT (filename) DO NOTHING;
