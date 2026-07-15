-- doc-ray: tenant-scoped Stripe billing state.
--
-- Stripe remains the payment processor and source of truth for card handling,
-- invoices, and hosted subscription management. These tables store only the
-- tenant/customer/subscription identifiers and the latest webhook-derived
-- subscription snapshot the app needs for authorization, audit, and the admin
-- console.

CREATE TABLE IF NOT EXISTS tenant_billing_accounts (
    tenant_id                uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    stripe_customer_id       text UNIQUE,
    billing_email            text,
    created_by_principal_id  uuid REFERENCES principals(id) ON DELETE SET NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tenant_subscriptions (
    tenant_id                 uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    stripe_customer_id        text NOT NULL,
    stripe_subscription_id    text UNIQUE,
    stripe_price_id           text,
    status                    text NOT NULL DEFAULT 'none',
    current_period_end        timestamptz,
    cancel_at_period_end      boolean NOT NULL DEFAULT false,
    latest_invoice_id         text,
    checkout_session_id       text,
    last_stripe_event_id      text,
    updated_at                timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_subscriptions_customer
    ON tenant_subscriptions (stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_tenant_subscriptions_status
    ON tenant_subscriptions (status);

CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    stripe_event_id text PRIMARY KEY,
    event_type      text NOT NULL,
    livemode        boolean NOT NULL DEFAULT false,
    payload         jsonb NOT NULL,
    processed_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stripe_webhook_events_type_time
    ON stripe_webhook_events (event_type, created_at DESC);

DROP TRIGGER IF EXISTS trg_tenant_billing_accounts_updated_at ON tenant_billing_accounts;
CREATE TRIGGER trg_tenant_billing_accounts_updated_at
    BEFORE UPDATE ON tenant_billing_accounts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_tenant_subscriptions_updated_at ON tenant_subscriptions;
CREATE TRIGGER trg_tenant_subscriptions_updated_at
    BEFORE UPDATE ON tenant_subscriptions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE tenant_billing_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_subscriptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_admin_select ON tenant_billing_accounts;
DROP POLICY IF EXISTS p_admin_insert ON tenant_billing_accounts;
DROP POLICY IF EXISTS p_admin_update ON tenant_billing_accounts;
CREATE POLICY p_admin_select ON tenant_billing_accounts
    FOR SELECT
    USING (tenant_id = docray_current_tenant() AND docray_is_admin());
CREATE POLICY p_admin_insert ON tenant_billing_accounts
    FOR INSERT
    WITH CHECK (tenant_id = docray_current_tenant() AND docray_is_admin());
CREATE POLICY p_admin_update ON tenant_billing_accounts
    FOR UPDATE
    USING (tenant_id = docray_current_tenant() AND docray_is_admin())
    WITH CHECK (tenant_id = docray_current_tenant() AND docray_is_admin());

DROP POLICY IF EXISTS p_admin_select ON tenant_subscriptions;
DROP POLICY IF EXISTS p_admin_insert ON tenant_subscriptions;
DROP POLICY IF EXISTS p_admin_update ON tenant_subscriptions;
CREATE POLICY p_admin_select ON tenant_subscriptions
    FOR SELECT
    USING (tenant_id = docray_current_tenant() AND docray_is_admin());
CREATE POLICY p_admin_insert ON tenant_subscriptions
    FOR INSERT
    WITH CHECK (tenant_id = docray_current_tenant() AND docray_is_admin());
CREATE POLICY p_admin_update ON tenant_subscriptions
    FOR UPDATE
    USING (tenant_id = docray_current_tenant() AND docray_is_admin())
    WITH CHECK (tenant_id = docray_current_tenant() AND docray_is_admin());

REVOKE ALL ON tenant_billing_accounts, tenant_subscriptions, stripe_webhook_events FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON tenant_billing_accounts, tenant_subscriptions TO docray_client;

INSERT INTO schema_migrations (filename)
VALUES ('080_billing.sql')
ON CONFLICT (filename) DO NOTHING;
