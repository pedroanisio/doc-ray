-- doc-ray: schema SemVer 1.53.0 — read/curation MCP endpoint split (no schema change).
--
-- Release 1.53.0 ships a BREAKING MCP surface change: the curation tools moved
-- from a widened /mcp to their own /mcp/curate endpoint (#36 Phase 2), so a
-- read-only caller no longer downloads the write surface it never invokes. No
-- DDL. This marker keeps doc_ray.__version__, engineering_decisions.yml,
-- CHANGELOG.md, and the database in lockstep
-- (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.53.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('145_schema_version_1_53_0.sql')
ON CONFLICT (filename) DO NOTHING;
