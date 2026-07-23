-- doc-ray: per-owner corpus isolation (supersedes migration 076).
--
-- doc-ray is a SaaS with open registration, so a newly registered user must
-- land on a clean, private corpus. Migration 076 made that impossible: its
-- role-permission branch granted every principal holding the 'read' action —
-- which is every registered reader — visibility of the whole tenant corpus,
-- minus documents tagged sensitivity=sensitive. With a single tenant, zero
-- document_acl rows and zero sensitivity tags, that resolved to "every user
-- reads every document". 076 called itself a stopgap for the missing granting
-- flow; ownership is the flow.
--
-- The read set becomes: tenant admin ∪ explicit ACL grant ∪ owned-by-caller.
-- owner_principal_id IS NULL means UNOWNED, not public: those rows stay
-- admin-only and are the seam the future org corpus plugs into (tenant_id
-- remains the org axis, owner_principal_id the user axis, so introducing
-- organizations later re-parents nothing).
--
-- Deleting a principal releases their documents to unowned rather than
-- cascading the corpus away with the account.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS owner_principal_id uuid
        REFERENCES principals(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS documents_owner_principal_idx
    ON documents (owner_principal_id)
    WHERE owner_principal_id IS NOT NULL;

COMMENT ON COLUMN documents.owner_principal_id IS
    'Owning principal (the uploader). NULL = unowned: admin-only today, reserved for the org corpus.';

-- Readable: admin over the tenant, explicit ACL grantee, or owner.
CREATE OR REPLACE FUNCTION docray_readable_documents() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT d.id
      FROM documents d
     WHERE d.tenant_id = docray_current_tenant()
       AND docray_is_admin()
    UNION
    SELECT d.id
      FROM documents d
      JOIN document_acl acl ON acl.document_id = d.id
     WHERE acl.principal_id = docray_current_principal()
       AND d.tenant_id = docray_current_tenant()
    UNION
    SELECT d.id
      FROM documents d
     WHERE d.tenant_id = docray_current_tenant()
       AND d.owner_principal_id = docray_current_principal()
$$;

-- Writable: owning a document implies curating it, so the owner branch joins
-- the admin and write-ACL branches. Migration 064's ABAC policy on sensitive
-- documents is a separate policy and is deliberately left untouched.
CREATE OR REPLACE FUNCTION docray_writable_documents() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT d.id
      FROM documents d
     WHERE d.tenant_id = docray_current_tenant()
       AND docray_is_admin()
    UNION
    SELECT d.id
      FROM documents d
      JOIN document_acl acl ON acl.document_id = d.id
     WHERE acl.principal_id = docray_current_principal()
       AND acl.permission IN ('annotate', 'delete', 'admin')
       AND d.tenant_id = docray_current_tenant()
    UNION
    SELECT d.id
      FROM documents d
     WHERE d.tenant_id = docray_current_tenant()
       AND d.owner_principal_id = docray_current_principal()
$$;

REVOKE ALL ON FUNCTION docray_readable_documents(), docray_writable_documents() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION docray_readable_documents(), docray_writable_documents()
    TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('102_document_ownership.sql')
    ON CONFLICT (filename) DO NOTHING;
