-- doc-ray: schema SemVer 1.53.1 — corpus sentence denominator repair (#42).
--
-- Pairs with migration 146. This patch release corrects the full corpus-stats
-- backfill and makes an unavailable denominator explicit on GraphQL and MCP.

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.53.1')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('147_schema_version_1_53_1.sql')
ON CONFLICT (filename) DO NOTHING;
