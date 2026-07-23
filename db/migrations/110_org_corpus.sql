-- doc-ray: the organization corpus — opt-in tenant-wide visibility (BL-139).
--
-- Per-owner isolation (102) and sharing (108) cover private documents and
-- person-to-person grants. This adds the third scope: an owner publishes a
-- document to their whole tenant, and every principal in that tenant can read
-- it. The tenant IS the organization, so org visibility is scoped to tenant_id
-- and never crosses tenants.
--
-- This is the SAFE inverse of the migration-076 leak. 076 defaulted every
-- document to visible and subtracted exceptions, so a single tenant with no
-- exceptions saw everything. Here documents default to `private`; only an
-- explicit, owner-authorized flip to `org` widens them. Nothing becomes visible
-- without a deliberate act, and the org-read branch is gated on visibility, not
-- on merely holding the read permission — so it cannot regress into 076.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS visibility text NOT NULL DEFAULT 'private';

DO $$
BEGIN
    ALTER TABLE documents
        ADD CONSTRAINT documents_visibility_check CHECK (visibility IN ('private', 'org'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

COMMENT ON COLUMN documents.visibility IS
    'private = owner/ACL/admin only; org = readable by every principal in the tenant. Default private.';

-- Readable: admin over the tenant, explicit ACL grantee, owner, OR the document
-- is published to the organization and the caller is in the same tenant.
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
    UNION
    SELECT d.id
      FROM documents d
     WHERE d.tenant_id = docray_current_tenant()
       AND d.visibility = 'org'
$$;

GRANT EXECUTE ON FUNCTION docray_readable_documents() TO docray_client, docray_batch;

-- Publishing to (or unpublishing from) the org is a share-class act: only the
-- owner or a tenant admin may do it. This SECURITY DEFINER function is the sole
-- write path for the column, so docray_client needs no UPDATE grant on
-- documents and an annotate-grantee cannot flip visibility. It enforces
-- owner-or-admin in the database (docray_can_share_document, migration 108),
-- not merely in the mutation layer.
CREATE OR REPLACE FUNCTION docray_set_document_visibility(doc_id uuid, vis text)
    RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF vis NOT IN ('private', 'org') THEN
        RAISE EXCEPTION 'invalid visibility %, expected private or org', vis;
    END IF;
    IF NOT docray_can_share_document(doc_id) THEN
        RAISE EXCEPTION 'not permitted to change visibility of document %', doc_id;
    END IF;
    UPDATE documents SET visibility = vis, updated_at = now() WHERE id = doc_id;
END
$$;

REVOKE ALL ON FUNCTION docray_set_document_visibility(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION docray_set_document_visibility(uuid, text)
    TO docray_client, docray_batch;

INSERT INTO schema_migrations (filename) VALUES ('110_org_corpus.sql')
    ON CONFLICT (filename) DO NOTHING;
