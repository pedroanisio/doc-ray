-- doc-ray: tenant-admin read access for Stripe webhook payment activity.
--
-- Webhook insertion remains app-owned. Tenant admins can read only events
-- attached to their tenant id or Stripe customer so the Billing panel can show
-- operational payment activity without exposing other tenants' Stripe payloads.

CREATE INDEX IF NOT EXISTS idx_stripe_webhook_events_object_customer_time
    ON stripe_webhook_events ((payload #>> '{data,object,customer}'), created_at DESC);

CREATE INDEX IF NOT EXISTS idx_stripe_webhook_events_object_tenant_time
    ON stripe_webhook_events ((payload #>> '{data,object,metadata,tenant_id}'), created_at DESC);

CREATE INDEX IF NOT EXISTS idx_stripe_webhook_events_client_ref_time
    ON stripe_webhook_events ((payload #>> '{data,object,client_reference_id}'), created_at DESC);

ALTER TABLE stripe_webhook_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_admin_select ON stripe_webhook_events;
CREATE POLICY p_admin_select ON stripe_webhook_events
    FOR SELECT
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
    );

GRANT SELECT ON stripe_webhook_events TO docray_client;

INSERT INTO schema_migrations (filename)
VALUES ('083_billing_event_read.sql')
ON CONFLICT (filename) DO NOTHING;
