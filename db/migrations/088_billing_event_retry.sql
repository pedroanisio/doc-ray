-- doc-ray: tenant-admin retry support for unprocessed Stripe billing events.
--
-- Tenant admins may retry only events already visible through the Billing panel.
-- The only mutable ledger field exposed to the client role is processed_at; event
-- insertion and payload mutation remain app-owned webhook-ingestion concerns.

ALTER TABLE stripe_webhook_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_admin_update_processed_at ON stripe_webhook_events;
CREATE POLICY p_admin_update_processed_at ON stripe_webhook_events
    FOR UPDATE
    USING (
        docray_is_admin()
        AND (
            payload #>> '{data,object,client_reference_id}' = docray_current_tenant()::text
            OR payload #>> '{data,object,metadata,tenant_id}' = docray_current_tenant()::text
            OR EXISTS (
                SELECT 1
                  FROM tenant_billing_accounts a
                 WHERE a.tenant_id = docray_current_tenant()
                   AND a.stripe_customer_id = payload #>> '{data,object,customer}'
            )
        )
    )
    WITH CHECK (
        docray_is_admin()
        AND (
            payload #>> '{data,object,client_reference_id}' = docray_current_tenant()::text
            OR payload #>> '{data,object,metadata,tenant_id}' = docray_current_tenant()::text
            OR EXISTS (
                SELECT 1
                  FROM tenant_billing_accounts a
                 WHERE a.tenant_id = docray_current_tenant()
                   AND a.stripe_customer_id = payload #>> '{data,object,customer}'
            )
        )
    );

GRANT UPDATE (processed_at) ON stripe_webhook_events TO docray_client;

INSERT INTO schema_migrations (filename)
VALUES ('088_billing_event_retry.sql')
ON CONFLICT (filename) DO NOTHING;
