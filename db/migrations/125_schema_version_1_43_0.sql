-- doc-ray: schema SemVer 1.43.0 — webapp experience epic (#23).
--
-- Application/API release marker following the learned-embedding cutover in
-- migration 124. The epic adds GraphQL, MCP, SDK/application, and webapp
-- capability over the existing schema; no additional structural DDL is needed.
-- Keeping an explicit marker preserves doc-ray's one-global-version contract.

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.43.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('125_schema_version_1_43_0.sql')
ON CONFLICT (filename) DO NOTHING;
