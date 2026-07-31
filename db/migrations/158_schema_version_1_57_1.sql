-- doc-ray: schema SemVer 1.57.1 — executable embedding read policy (BL-205).
--
-- Migration 157 rewrites the `embedding` p_read policy so a corpus-wide vector
-- search is planned as a per-row primary-key probe instead of a semi-join over
-- every readable span. A row-level security policy is part of the schema
-- contract, so the change carries a version even though no table, column or
-- function is touched.
--
-- PATCH, not minor: visibility is unchanged and no surface gains or loses a
-- capability. `semantic_search_sentences` was always specified to answer
-- corpus-wide queries; it simply could not, and now does.
--
-- Keeps doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md and the
-- database in lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.57.1')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('158_schema_version_1_57_1.sql')
ON CONFLICT (filename) DO NOTHING;
