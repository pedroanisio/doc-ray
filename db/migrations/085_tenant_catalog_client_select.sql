-- doc-ray: repair tenant catalogue read access for client-role billing status.
--
-- Billing status now returns the real tenant slug/display_name. Existing
-- deployments that already recorded migration 061 need this forward migration
-- because 061 will not be replayed by the runtime migrator.

GRANT SELECT ON tenants TO docray_client;

INSERT INTO schema_migrations (filename)
VALUES ('085_tenant_catalog_client_select.sql')
ON CONFLICT (filename) DO NOTHING;
