"""Tenant Stripe billing regression tests — admin."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any

from _billing_test_support import (
    FakeStripe,
    _apply_billing_migration,
    _cfg,
)
from _mutations_common import WRITE_PRINCIPAL, _aconn_as
from fastapi.testclient import TestClient

from doc_ray.application import billing
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema


def test_billing_admin_retries_pending_invoice_event_while_entitlement_blocked(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    schema = build_schema()
    conn.execute(
        """
        INSERT INTO tenant_billing_accounts
            (tenant_id, stripe_customer_id, billing_email, created_by_principal_id)
        VALUES (%s, 'cus_admin_retry_pending', 'retry@example.com', %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            billing_email = EXCLUDED.billing_email
        """,
        (tenant_id, WRITE_PRINCIPAL.principal_id),
    )
    invoice_event = {
        "id": "evt_admin_retry_paid_invoice_1",
        "type": "invoice.paid",
        "livemode": False,
        "data": {
            "object": {
                "id": "in_admin_retry_paid",
                "customer": "cus_admin_retry_pending",
                "subscription": "sub_admin_retry_pending",
                "status": "paid",
                "amount_paid": 4900,
                "currency": "usd",
            }
        },
    }
    subscription_event = {
        "id": "evt_admin_retry_subscription_1",
        "type": "customer.subscription.created",
        "livemode": False,
        "data": {
            "object": {
                "id": "sub_admin_retry_pending",
                "customer": "cus_admin_retry_pending",
                "status": "past_due",
                "current_period_end": int(time.time()),
                "cancel_at_period_end": False,
                "latest_invoice": "in_prior_failed",
                "items": {"data": [{"price": {"id": "price_test_monthly"}}]},
                "metadata": {"tenant_id": tenant_id},
            }
        },
    }

    async def create_pending_event() -> None:
        aconn = await _aconn_as(None)
        try:
            await aconn.execute("RESET ROLE")
            try:
                await billing.process_webhook_event(aconn, invoice_event, _cfg())
            except billing.WebhookProcessingPending as exc:
                assert "retry" in str(exc).lower()
            else:  # pragma: no cover - defensive
                raise AssertionError("invoice with no subscription snapshot was processed")
            assert await billing.process_webhook_event(aconn, subscription_event, _cfg()) is True
        finally:
            await aconn.close()

    async def retry_from_graphql() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=WRITE_PRINCIPAL,
                loaders=Loaders(aconn),
                settings=_cfg(enforcement_enabled=True),
            )
            result = await schema.execute(
                """
                mutation RetryBillingEvent($stripeEventId: ID!) {
                  retryBillingEvent(stripeEventId: $stripeEventId) {
                    stripeEventId
                    eventType
                    objectStatus
                    amountPaid
                    currency
                    processedAt
                  }
                }
                """,
                variable_values={"stripeEventId": "evt_admin_retry_paid_invoice_1"},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            retried = result.data["retryBillingEvent"]
            assert retried["stripeEventId"] == "evt_admin_retry_paid_invoice_1"
            assert retried["eventType"] == "invoice.paid"
            assert retried["objectStatus"] == "paid"
            assert retried["amountPaid"] == 4900
            assert retried["currency"] == "usd"
            assert retried["processedAt"] is not None
        finally:
            await aconn.close()

    try:
        asyncio.run(create_pending_event())
        blocked_state = conn.execute(
            """
            SELECT status, latest_invoice_id
            FROM tenant_subscriptions
            WHERE tenant_id = %s
            """,
            (tenant_id,),
        ).fetchone()
        assert blocked_state["status"] == "past_due"
        assert blocked_state["latest_invoice_id"] == "in_prior_failed"
        pending_row = conn.execute(
            """
            SELECT processed_at IS NULL AS pending
            FROM stripe_webhook_events
            WHERE stripe_event_id = 'evt_admin_retry_paid_invoice_1'
            """
        ).fetchone()
        assert pending_row["pending"] is True

        asyncio.run(retry_from_graphql())
        row = conn.execute(
            """
            SELECT status, latest_invoice_id, last_stripe_event_id
            FROM tenant_subscriptions
            WHERE tenant_id = %s
            """,
            (tenant_id,),
        ).fetchone()
        assert row["status"] == "active"
        assert row["latest_invoice_id"] == "in_admin_retry_paid"
        assert row["last_stripe_event_id"] == "evt_admin_retry_paid_invoice_1"
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id IN "
            "('evt_admin_retry_paid_invoice_1', 'evt_admin_retry_subscription_1')"
        )


def test_billing_hosted_payment_mutations_reject_read_scoped_admin(pg, monkeypatch):
    conn, _created = pg
    _apply_billing_migration(conn)
    stripe_calls: list[str] = []

    async def fake_stripe_post(
        self: billing.StripeRestTransport,
        path: str,
        form: dict[str, str],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        stripe_calls.append(path)
        raise AssertionError("read-scoped billing mutation reached Stripe")

    monkeypatch.setattr(billing.StripeRestTransport, "_post", fake_stripe_post)
    cfg = replace(
        _cfg(),
        api_tokens={"billing-read-token": (WRITE_PRINCIPAL.principal_id, frozenset({"read"}))},
        api_allow_anonymous=False,
    )
    app = create_app(cfg)

    try:
        with TestClient(app) as client:
            checkout = client.post(
                "/graphql",
                json={
                    "query": """
                    mutation {
                      createBillingCheckoutSession {
                        url
                      }
                    }
                    """
                },
                headers={"authorization": "Bearer billing-read-token"},
            )
            portal = client.post(
                "/graphql",
                json={
                    "query": """
                    mutation {
                      createBillingPortalSession {
                        url
                      }
                    }
                    """
                },
                headers={"authorization": "Bearer billing-read-token"},
            )

        assert checkout.status_code == 200
        assert portal.status_code == 200
        checkout_body = checkout.json()
        portal_body = portal.json()
        assert "write scope" in checkout_body["errors"][0]["message"]
        assert "write scope" in portal_body["errors"][0]["message"]
        assert stripe_calls == []
    finally:
        conn.execute(
            "DELETE FROM tenant_subscriptions WHERE tenant_id = "
            "(SELECT tenant_id FROM tenants WHERE slug = 'billing-test')"
        )
        conn.execute(
            "DELETE FROM tenant_billing_accounts WHERE tenant_id = "
            "(SELECT tenant_id FROM tenants WHERE slug = 'billing-test')"
        )


def test_checkout_session_creates_customer_and_graphql_status(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    fake = FakeStripe()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            url = await billing.create_checkout_session(aconn, _cfg(), transport=fake)
            assert url == "https://checkout.stripe.test/session"
            assert fake.created_customers[0]["metadata"] == {"tenant_id": tenant_id}
            assert fake.created_customers[0]["idempotency_key"] == (f"docray.customer.{tenant_id}")
            assert fake.checkout_calls[0]["price_id"] == "price_test_monthly"
            assert fake.checkout_calls[0]["idempotency_key"].startswith(
                f"docray.checkout.{tenant_id}."
            )
            assert fake.checkout_calls[0]["subscription_metadata"] == {
                "tenant_id": tenant_id,
                "price_id": "price_test_monthly",
            }

            schema = build_schema()
            ctx = RequestContext(
                conn=aconn,
                principal=WRITE_PRINCIPAL,
                loaders=Loaders(aconn),
                settings=_cfg(),
            )
            result = await schema.execute(
                """
                query {
                  billingStatus {
                    tenantId
                    tenantSlug
                    tenantName
                    stripeCustomerId
                    subscriptionStatus
                    checkoutSessionId
                    checkoutReady
                    portalReady
                    webhookReady
                    webhookEndpointUrl
                    liveModeReady
                    missingConfiguration
                    configurationIssues
                    requiredWebhookEvents
                    enforcementEnabled
                    entitled
                    entitlementReason
                  }
                }
                """,
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            status = result.data["billingStatus"]
            assert status["tenantId"] == tenant_id
            assert status["tenantSlug"] == "default"
            assert status["tenantName"] == "Default tenant"
            assert status["stripeCustomerId"] == "cus_test_123"
            assert status["subscriptionStatus"] == "checkout_pending"
            assert status["checkoutSessionId"] == "cs_test_123"
            assert status["checkoutReady"] is True
            assert status["portalReady"] is True
            assert status["webhookReady"] is True
            assert status["webhookEndpointUrl"] == "https://app.example.com/stripe/webhook"
            assert status["liveModeReady"] is False
            assert status["missingConfiguration"] == ["DOCRAY_BILLING_ENFORCEMENT_ENABLED=true"]
            assert status["configurationIssues"] == []
            assert "checkout.session.completed" in status["requiredWebhookEvents"]
            assert "checkout.session.expired" in status["requiredWebhookEvents"]
            assert "invoice.paid" in status["requiredWebhookEvents"]
            assert "invoice.payment_succeeded" in status["requiredWebhookEvents"]
            assert "invoice.finalization_failed" in status["requiredWebhookEvents"]
            assert status["enforcementEnabled"] is False
            assert status["entitled"] is True
            assert status["entitlementReason"] == "billing enforcement is disabled"

            try:
                await billing.create_checkout_session(aconn, _cfg(), transport=fake)
            except billing.BillingConfigurationError as exc:
                assert "checkout_pending Stripe subscription state" in str(exc)
            else:  # pragma: no cover - defensive
                raise AssertionError("pending tenant was allowed to start duplicate Checkout")
            assert len(fake.checkout_calls) == 1
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))


def test_checkout_session_requires_signed_webhook_processing_even_in_test_mode(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    fake = FakeStripe()
    cfg = replace(_cfg(), stripe_webhook_secret="")

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            try:
                await billing.create_checkout_session(aconn, cfg, transport=fake)
            except billing.BillingConfigurationError as exc:
                message = str(exc)
                assert "Stripe webhook processing is not configured" in message
                assert "DOCRAY_STRIPE_WEBHOOK_SECRET" in message
            else:  # pragma: no cover - defensive
                raise AssertionError("Checkout started without signed webhook processing")
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
        assert fake.created_customers == []
        assert fake.checkout_calls == []
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))


def test_portal_session_requires_signed_webhook_processing_even_in_test_mode(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    fake = FakeStripe()
    cfg = replace(_cfg(), stripe_webhook_secret="")
    conn.execute(
        """
        INSERT INTO tenant_billing_accounts
            (tenant_id, stripe_customer_id, billing_email, created_by_principal_id)
        VALUES (%s, 'cus_test_portal_no_webhook', 'billing@example.com', %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            billing_email = EXCLUDED.billing_email
        """,
        (tenant_id, WRITE_PRINCIPAL.principal_id),
    )

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            try:
                await billing.create_portal_session(aconn, cfg, transport=fake)
            except billing.BillingConfigurationError as exc:
                message = str(exc)
                assert "Stripe webhook processing is not configured" in message
                assert "DOCRAY_STRIPE_WEBHOOK_SECRET" in message
            else:  # pragma: no cover - defensive
                raise AssertionError("Portal started without signed webhook processing")
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
        assert fake.portal_calls == []
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))


def test_live_mode_readiness_rejects_test_key_and_insecure_payment_urls(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    schema = build_schema()
    unsafe_cfg = replace(
        _cfg(enforcement_enabled=True),
        stripe_success_url="http://app.example.com/billing/success",
        stripe_cancel_url="http://app.example.com/billing/cancel",
        stripe_portal_return_url="http://app.example.com/billing",
        public_base_url="http://app.example.com",
    )

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=WRITE_PRINCIPAL,
                loaders=Loaders(aconn),
                settings=unsafe_cfg,
            )
            result = await schema.execute(
                """
                query {
                  billingStatus {
                    tenantId
                    checkoutReady
                    portalReady
                    webhookReady
                    liveModeReady
                    missingConfiguration
                    configurationIssues
                  }
                }
                """,
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            status = result.data["billingStatus"]
            assert status["tenantId"] == tenant_id
            assert status["checkoutReady"] is True
            assert status["portalReady"] is True
            assert status["webhookReady"] is True
            assert status["missingConfiguration"] == []
            assert status["liveModeReady"] is False
            assert status["configurationIssues"] == [
                "DOCRAY_STRIPE_SECRET_KEY uses a Stripe test-mode key",
                "DOCRAY_STRIPE_SUCCESS_URL must use https for live billing",
                "DOCRAY_STRIPE_CANCEL_URL must use https for live billing",
                "DOCRAY_STRIPE_PORTAL_RETURN_URL must use https for live billing",
                "DOCRAY_PUBLIC_BASE_URL must use https for live billing",
            ]
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
