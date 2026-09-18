-- doc-ray: schema SemVer 1.45.0 — genre / type classification (issue #6).
--
-- Migration 127 adds the additive `document_classification` table (a new genre
-- axis + the input to the per-entity-type resolution policy). An additive table
-- is a MINOR bump under doc-ray's one-global-version contract; this marker keeps
-- doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md, and the database
-- in lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.45.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('128_schema_version_1_45_0.sql')
ON CONFLICT (filename) DO NOTHING;
