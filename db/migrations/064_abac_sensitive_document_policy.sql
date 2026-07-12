-- doc-ray: ABAC helper for sensitive document write policy (BL-073).
--
-- RBAC/document_acl answers "may this principal write this document at all".
-- This helper layers a resource-attribute rule on top: documents marked with
-- document_metadata.sensitivity=sensitive require an additional explicit
-- 'sensitive' ACL grant unless the caller is an admin.

INSERT INTO permissions (action) VALUES ('sensitive')
    ON CONFLICT (action) DO NOTHING;

CREATE OR REPLACE FUNCTION docray_audit_tenant_for_principal(actor uuid)
RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT tenant_id FROM principals WHERE id = actor
$$;

CREATE OR REPLACE FUNCTION docray_abac_document_write_allowed(target_document uuid)
RETURNS TABLE(ok boolean, reason text)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    WITH attrs AS (
        SELECT EXISTS (
            SELECT 1
              FROM document_metadata dm
             WHERE dm.document_id = target_document
               AND dm.key = 'sensitivity'
               AND lower(dm.value) = 'sensitive'
        ) AS is_sensitive
    ),
    grants AS (
        SELECT
            docray_is_admin() AS is_admin,
            EXISTS (
                SELECT 1
                  FROM document_acl acl
                 WHERE acl.document_id = target_document
                   AND acl.principal_id = docray_current_principal()
                   AND acl.permission = 'sensitive'
            ) AS has_sensitive_grant
    )
    SELECT
        (NOT attrs.is_sensitive OR grants.is_admin OR grants.has_sensitive_grant) AS ok,
        CASE
            WHEN NOT attrs.is_sensitive THEN 'allowed'
            WHEN grants.is_admin THEN 'allowed_admin'
            WHEN grants.has_sensitive_grant THEN 'allowed_sensitive_grant'
            ELSE 'sensitive_document_requires_explicit_grant'
        END AS reason
      FROM attrs, grants
$$;

REVOKE ALL ON FUNCTION docray_audit_tenant_for_principal(uuid),
                       docray_abac_document_write_allowed(uuid)
    FROM PUBLIC;
-- The audit_events INSERT policy added in 063 calls these helpers directly.
-- Granting EXECUTE lets the app role satisfy the policy without exposing the
-- underlying principals table.
GRANT EXECUTE ON FUNCTION docray_current_principal(), docray_current_tenant()
    TO docray_client;
GRANT EXECUTE ON FUNCTION docray_audit_tenant_for_principal(uuid) TO docray_client;
GRANT EXECUTE ON FUNCTION docray_abac_document_write_allowed(uuid) TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('064_abac_sensitive_document_policy.sql')
    ON CONFLICT (filename) DO NOTHING;
