-- doc-ray: tenant-scoped SAML identity-provider configuration (BL-067).
--
-- SAML is configured per tenant and maps assertions back into the same
-- principals.external_subject identity surface used by OIDC. XMLDSIG
-- verification is intentionally fail-closed unless a tenant explicitly opts
-- into unsigned assertions for local/test IdPs.

CREATE TABLE IF NOT EXISTS tenant_saml_identity_providers (
    tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    entity_id text NOT NULL,
    metadata_url text,
    metadata_xml text,
    sso_url text,
    x509_cert_pem text,
    service_provider_entity_id text NOT NULL,
    acs_url text,
    allow_unsigned_assertions boolean NOT NULL DEFAULT false,
    jit_provisioning boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (metadata_url IS NOT NULL OR metadata_xml IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_tenant_saml_idp_entity
    ON tenant_saml_identity_providers (entity_id);

INSERT INTO schema_migrations (filename) VALUES ('067_saml_identity_providers.sql')
    ON CONFLICT (filename) DO NOTHING;
