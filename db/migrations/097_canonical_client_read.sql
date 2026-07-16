-- doc-ray: grant the API/webapp role SELECT on the shared canonical registry.
--
-- BUG: GET /doc/<id>?view=original&overlay=entities returned 500
--   `psycopg.errors.InsufficientPrivilege: permission denied for table canonical`
-- (webapp/overlay_web.py entity_overlay_boxes / markup_highlights join canonical
-- to resolve a linked entity's preferred display name). The webapp runs as
-- docray_api, which inherits docray_client (migration 030: docray_api IN ROLE
-- docray_client); docray_client's SELECT allowlist in 018_rbac.sql was written
-- BEFORE the canonical table existed (migration 021) and never added it.
--
-- canonical is a shared registry (one row per real-world entity; no tenant_id,
-- no document payload) — the same class as lexemes/authors/tags, which 018
-- already grants to docray_client. Confidentiality is enforced the same way as
-- for those registries: the entity-overlay queries only reach canonical rows via
-- entity_link -> entity_mention -> annotations (all RLS-scoped by document), so a
-- caller only ever resolves labels for entities their own documents reference.

GRANT SELECT ON canonical TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('097_canonical_client_read.sql')
    ON CONFLICT (filename) DO NOTHING;
