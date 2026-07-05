-- doc-ray: runtime grants for the authenticated API role (BL-040).
--
-- Migration 030 moved HTTP requests to docray_api/docray_client so RLS applies
-- instead of leaving anonymous traffic on the trusted superuser connection.
-- The queue and seed-suggestion tables were added after the original RLS
-- allowlist, so they need explicit privileges and document-scoped policies.

GRANT SELECT, INSERT ON jobs TO docray_client;

ALTER TABLE seed_suggestion ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_read ON seed_suggestion;
DROP POLICY IF EXISTS p_upd ON seed_suggestion;

CREATE POLICY p_read ON seed_suggestion
    FOR SELECT
    USING (document_id IN (SELECT * FROM docray_readable_documents()));

CREATE POLICY p_upd ON seed_suggestion
    FOR UPDATE
    USING (document_id IN (SELECT * FROM docray_writable_documents()))
    WITH CHECK (document_id IN (SELECT * FROM docray_writable_documents()));

GRANT SELECT, UPDATE ON seed_suggestion TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('032_api_role_runtime_grants.sql')
    ON CONFLICT (filename) DO NOTHING;
