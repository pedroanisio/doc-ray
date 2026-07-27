-- doc-ray: schema SemVer 1.48.2 — arXiv source adapter + ingest fixes (no schema change).
--
-- Release 1.48.2 ships code-only changes: the DocumentSource port with its first
-- adapter (arXiv) over a bytes-first ingest core, and the Spaces loader fixes
-- (unfixable extraction failures no longer re-downloaded and relabelled
-- "duplicate"; every intake path refreshes the read-model rollup through one
-- shared finalizer; --limit counts ingests, not objects considered). No DDL.
-- This marker keeps doc_ray.__version__, engineering_decisions.yml,
-- CHANGELOG.md, and the database in lockstep
-- (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.48.2')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('136_schema_version_1_48_2.sql')
ON CONFLICT (filename) DO NOTHING;
