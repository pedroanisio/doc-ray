-- doc-ray: schema SemVer 1.48.1 — BL-162 release marker (no schema change).
--
-- Release 1.48.1 ships code-only fixes: the MCP page-size clamp made visible
-- (coverage.requested_limit + clamp note, last-row cursor docstrings) and CLI
-- ingest_paths enqueueing DEFAULT_PIPELINE_CHAIN + refreshing document_stats
-- (parity with handle_ingest and the Spaces loader). No DDL. This marker keeps
-- doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md, and the
-- database in lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.48.1')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('135_schema_version_1_48_1.sql')
ON CONFLICT (filename) DO NOTHING;
