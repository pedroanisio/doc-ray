-- doc-ray: tenant foundation for corpus partitioning.
--
-- This migration keeps the existing single-tenant deployment behavior by
-- seeding a default tenant, then makes principals and documents tenant-owned.
-- The RLS document helpers are tightened so ACL mistakes cannot cross tenant
-- boundaries.

CREATE TABLE IF NOT EXISTS tenants (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         text NOT NULL UNIQUE
                   CHECK (slug ~ '^[a-z0-9][a-z0-9-]*$'),
    display_name text NOT NULL,
    disabled_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE tenants IS 'Tenant partition catalogue; the default tenant preserves existing deployments.';

INSERT INTO tenants (slug, display_name)
VALUES ('default', 'Default tenant')
ON CONFLICT (slug) DO UPDATE
SET display_name = EXCLUDED.display_name;

CREATE OR REPLACE FUNCTION docray_default_tenant_id() RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT id FROM tenants WHERE slug = 'default' LIMIT 1
$$;

ALTER TABLE principals
    ADD COLUMN IF NOT EXISTS tenant_id uuid REFERENCES tenants(id)
        DEFAULT docray_default_tenant_id();

UPDATE principals
SET tenant_id = docray_default_tenant_id()
WHERE tenant_id IS NULL;

ALTER TABLE principals
    ALTER COLUMN tenant_id SET NOT NULL;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS tenant_id uuid REFERENCES tenants(id)
        DEFAULT docray_default_tenant_id();

UPDATE documents
SET tenant_id = docray_default_tenant_id()
WHERE tenant_id IS NULL;

ALTER TABLE documents
    ALTER COLUMN tenant_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_principals_tenant ON principals (tenant_id);
CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents (tenant_id);

CREATE OR REPLACE FUNCTION docray_current_tenant() RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT p.tenant_id
      FROM principals p
     WHERE p.id = docray_current_principal()
     LIMIT 1
$$;

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
$$;

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
$$;

REVOKE ALL ON FUNCTION docray_default_tenant_id(), docray_current_tenant(),
                       docray_readable_documents(), docray_writable_documents()
    FROM PUBLIC;

GRANT EXECUTE ON FUNCTION docray_readable_documents(), docray_writable_documents()
    TO docray_client;

GRANT SELECT ON tenants TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('061_tenant_foundation.sql')
    ON CONFLICT (filename) DO NOTHING;
