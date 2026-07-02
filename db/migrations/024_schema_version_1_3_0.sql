-- doc-ray: bump the schema's semantic version 1.0.0 -> 1.3.0.
--
-- Reconciles the queryable schema contract (schema_version / docray_schema_version())
-- with three backward-compatible additions that landed after the 1.0.0 baseline
-- (migration 019) without a version bump — SemVer drift this migration closes:
--   021_canonical.sql   -> 1.1.0  (canonical entity registry + remissive index)
--   022_form_index.sql  -> 1.2.0  (index-first candidate-generation form index)
--   023_resolution.sql  -> 1.3.0  (Stage-3 resolution review store)
-- Each is an additive, backward-compatible change (new tables only) = MINOR bump;
-- three of them advance 1.0.0 -> 1.3.0. 1.3.0 is also the project's unified global
-- version (doc_ray.__version__ / pyproject); the two are kept in lockstep by
-- scripts/check_version_consistency.py.

UPDATE schema_version
   SET version = '1.3.0', updated_at = now()
 WHERE singleton;

INSERT INTO schema_migrations (filename) VALUES ('024_schema_version_1_3_0.sql')
    ON CONFLICT (filename) DO NOTHING;
