-- doc-ray: role-based corpus read visibility (auth-audit MAJOR).
--
-- docray_readable_documents() was admin ∪ per-document ACL only, so a freshly
-- registered reader (principal + reader role, zero ACL rows — migration 074's
-- design) saw an empty corpus and no granting flow existed. This adds the
-- role-permission branch: any principal whose role carries the 'read'
-- permission (reader/curator/admin per 018_rbac) sees the tenant's corpus,
-- EXCEPT documents marked sensitivity=sensitive in document_metadata — those
-- remain explicitly ACL-gated, matching 064's ABAC write policy, so the new
-- branch cannot leak restricted material. Writable documents are unchanged.

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
       AND EXISTS (
               SELECT 1
                 FROM principal_roles pr
                 JOIN role_permissions rp ON rp.role_id = pr.role_id
                 JOIN permissions p       ON p.id = rp.permission_id
                WHERE pr.principal_id = docray_current_principal()
                  AND p.action = 'read'
           )
       AND NOT EXISTS (
               SELECT 1
                 FROM document_metadata dm
                WHERE dm.document_id = d.id
                  AND dm.key = 'sensitivity'
                  AND lower(dm.value) = 'sensitive'
           )
$$;

INSERT INTO schema_migrations (filename) VALUES ('076_reader_corpus_read.sql')
    ON CONFLICT (filename) DO NOTHING;
