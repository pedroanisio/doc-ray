-- doc-ray: schema SemVer 1.49.0 — reference intake reaches every surface (no schema change).
--
-- Release 1.49.0 ships code-only changes: ingestDocument accepts a remote
-- reference alongside an upload (webapp form field, MCP ingest_document tool),
-- a failed document renders the reason it failed, and the admin console's
-- status filter offers the values the corpus actually stores. No DDL. This
-- marker keeps doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md,
-- and the database in lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.49.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('137_schema_version_1_49_0.sql')
ON CONFLICT (filename) DO NOTHING;
