-- doc-ray: owner-driven document sharing (Phase 3).
--
-- Per-owner isolation (migration 102) gave each user a private corpus, but only
-- the admin CLI could write document_acl rows — a user had no way to hand a
-- document to a colleague. This opens the grant/revoke path to owners, enforced
-- at the database rather than trusting the mutation layer:
--
--   * docray_client gains INSERT/DELETE on document_acl (it already had none).
--   * document_acl is ENABLE + FORCE ROW LEVEL SECURITY (Phase 2's rule applied
--     to the new write path). Without a policy, granting INSERT would let any
--     principal forge a grant for someone else's document. The policies scope
--     writes to documents the caller OWNS (or, for an admin, any in the tenant),
--     and cap the grantable permissions at read/annotate — sharing must never
--     become a path to admin/redact.
--   * The batch writer (docray_batch, BYPASSRLS from migration 106) bypasses
--     these policies entirely, so the admin CLI's grant_document — which assigns
--     arbitrary permissions — is unaffected. auth_admin therefore assumes
--     docray_batch, exactly as adapters/persistence.connect() does.
--
-- Sharing targets a colleague by LOGIN EMAIL, resolved by a SECURITY DEFINER
-- helper scoped to the caller's tenant. That is a bounded enumeration surface
-- (a member can probe which emails exist in their own tenant), accepted as the
-- cost of email-based sharing and contained by the tenant scope.

-- ============================ email resolution =============================

CREATE OR REPLACE FUNCTION docray_principal_by_email(email text) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT pc.principal_id
      FROM principal_credentials pc
      JOIN principals p ON p.id = pc.principal_id
     WHERE pc.email_normalized = lower(btrim(email))
       AND p.tenant_id = docray_current_tenant()
     LIMIT 1
$$;

REVOKE ALL ON FUNCTION docray_principal_by_email(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION docray_principal_by_email(text) TO docray_client, docray_batch;

-- The reverse lookup, for rendering "shared with <email>". docray_client has no
-- SELECT on principal_credentials (which also holds password hashes), and that
-- table has no RLS — so a direct join would leak every tenant's emails. This
-- SECURITY DEFINER function exposes ONLY the email, and only for principals in
-- the caller's own tenant.
CREATE OR REPLACE FUNCTION docray_email_for_principal(pid uuid) RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT pc.email_normalized
      FROM principal_credentials pc
      JOIN principals p ON p.id = pc.principal_id
     WHERE pc.principal_id = pid
       AND p.tenant_id = docray_current_tenant()
     LIMIT 1
$$;

REVOKE ALL ON FUNCTION docray_email_for_principal(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION docray_email_for_principal(uuid) TO docray_client, docray_batch;

-- ============================ share authority ==============================
-- May the caller change who a document is shared with? The batch writer always
-- may (the admin CLI); otherwise the caller must be a tenant admin or the
-- document's owner. Not merely "writable" — an annotate-grantee must not be able
-- to re-share a document they were lent.

CREATE OR REPLACE FUNCTION docray_can_share_document(doc_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT EXISTS (
               SELECT 1 FROM documents d
                WHERE d.id = doc_id
                  AND d.tenant_id = docray_current_tenant()
                  AND (docray_is_admin() OR d.owner_principal_id = docray_current_principal())
           )
$$;

REVOKE ALL ON FUNCTION docray_can_share_document(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION docray_can_share_document(uuid) TO docray_client, docray_batch;

-- ============================ acl write path ===============================

GRANT SELECT, INSERT, DELETE ON document_acl TO docray_client;

ALTER TABLE document_acl ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_acl_read ON document_acl;
DROP POLICY IF EXISTS p_acl_ins ON document_acl;
DROP POLICY IF EXISTS p_acl_del ON document_acl;

-- Read: the sharer sees the grants they manage; a grantee sees their own row.
CREATE POLICY p_acl_read ON document_acl FOR SELECT
    USING (docray_can_share_document(document_id)
           OR principal_id = docray_current_principal());

-- Insert: only on a document the caller may share, and only the non-escalating
-- permissions. The batch writer (admin CLI) has BYPASSRLS, so this policy never
-- constrains its arbitrary-permission grants — only principal-facing sharing.
CREATE POLICY p_acl_ins ON document_acl FOR INSERT
    WITH CHECK (docray_can_share_document(document_id)
                AND permission IN ('read', 'annotate'));

-- Delete: revoking is the same authority as granting.
CREATE POLICY p_acl_del ON document_acl FOR DELETE
    USING (docray_can_share_document(document_id));

ALTER TABLE document_acl FORCE ROW LEVEL SECURITY;

INSERT INTO schema_migrations (filename) VALUES ('108_document_sharing.sql')
    ON CONFLICT (filename) DO NOTHING;
