-- doc-ray: schema SemVer 1.55.0 — caption retraction (BL-201).
--
-- Migration 150 adds visual_caption.retracted_at / retracted_by /
-- retraction_reason and a partial index over them. Additive: no table, column
-- or policy changes to existing objects, and no signature altered. This marker
-- keeps doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md and the
-- database in lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.55.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('151_schema_version_1_55_0.sql')
ON CONFLICT (filename) DO NOTHING;
