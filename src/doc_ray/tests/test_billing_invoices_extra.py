"""Tenant Stripe billing regression tests — invoices_extra."""

from __future__ import annotations

import asyncio
import time

from _billing_test_support import (
    _apply_billing_migration,
    _cfg,
)
from _mutations_common import WRITE_PRINCIPAL, _aconn_as

from doc_ray.application import billing
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema


def test_paid_invoice_does_not_resurrect_canceled_subscription(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    schema = build_schema()
    conn.execute(
        """
        INSERT INTO tenant_billing_accounts
            (tenant_id, stripe_customer_id, billing_email, created_by_principal_id)
        VALUES (%s, 'cus_canceled_invoice', 'canceled@example.com', %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            billing_email = EXCLUDED.billing_email
        """,
        (tenant_id, WRITE_PRINCIPAL.principal_id),
    )
    conn.execute(
        """
        INSERT INTO tenant_subscriptions
            (tenant_id, stripe_customer_id, stripe_subscription_id, status, latest_invoice_id)
        VALUES (%s, 'cus_canceled_invoice', 'sub_canceled_invoice',
                'canceled', 'in_canceled_prior')
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_subscription_id = EXCLUDED.stripe_subscription_id,
            status = EXCLUDED.status,
            latest_invoice_id = EXCLUDED.latest_invoice_id
        """,
        (tenant_id,),
    )
    event = {
        "id": "evt_invoice_canceled_paid_1",
        "type": "invoice.paid",
        "livemode": False,
        "data": {
            "object": {
                "id": "in_canceled_late_paid",
                "customer": "cus_canceled_invoice",
                "subscription": "sub_canceled_invoice",
                "status": "paid",
            }
        },
    }

    async def process() -> None:
        aconn = await _aconn_as(None)
        try:
            await aconn.execute("RESET ROLE")
            assert await billing.process_webhook_event(aconn, event) is True
        finally:
            await aconn.close()

    async def check_status() -> None:
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
                query {
                  billingStatus {
                    subscriptionStatus
                    latestInvoiceId
                    entitled
                    entitlementReason
                  }
                }
                """,
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["billingStatus"] == {
                "subscriptionStatus": "canceled",
                "latestInvoiceId": "in_canceled_late_paid",
                "entitled": False,
                "entitlementReason": "subscription is canceled",
            }
        finally:
            await aconn.close()

    try:
        asyncio.run(process())
        row = conn.execute(
            """
            SELECT status, latest_invoice_id, last_stripe_event_id
            FROM tenant_subscriptions
            WHERE tenant_id = %s
            """,
            (tenant_id,),
        ).fetchone()
        assert row["status"] == "canceled"
        assert row["latest_invoice_id"] == "in_canceled_late_paid"
        assert row["last_stripe_event_id"] == "evt_invoice_canceled_paid_1"
        asyncio.run(check_status())
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id = "
            "'evt_invoice_canceled_paid_1'"
        )


def test_invoice_event_does_not_update_different_subscription_for_same_customer(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    conn.execute(
        """
        INSERT INTO tenant_billing_accounts
            (tenant_id, stripe_customer_id, billing_email, created_by_principal_id)
        VALUES (%s, 'cus_shared_subscription', 'shared@example.com', %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            billing_email = EXCLUDED.billing_email
        """,
        (tenant_id, WRITE_PRINCIPAL.principal_id),
    )
    conn.execute(
        """
        INSERT INTO tenant_subscriptions
            (tenant_id, stripe_customer_id, stripe_subscription_id, status, latest_invoice_id)
        VALUES (%s, 'cus_shared_subscription', 'sub_current', 'active', 'in_current')
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_subscription_id = EXCLUDED.stripe_subscription_id,
            status = EXCLUDED.status,
            latest_invoice_id = EXCLUDED.latest_invoice_id
        """,
        (tenant_id,),
    )
    event = {
        "id": "evt_wrong_subscription_invoice_1",
        "type": "invoice.payment_failed",
        "livemode": False,
        "data": {
            "object": {
                "id": "in_wrong_subscription",
                "customer": "cus_shared_subscription",
                "subscription": "sub_other",
                "status": "open",
            }
        },
    }

    async def check() -> None:
        aconn = await _aconn_as(None)
        try:
            await aconn.execute("RESET ROLE")
            assert await billing.process_webhook_event(aconn, event) is True
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
        row = conn.execute(
            """
            SELECT status, latest_invoice_id, last_stripe_event_id
            FROM tenant_subscriptions
            WHERE tenant_id = %s
            """,
            (tenant_id,),
        ).fetchone()
        assert row["status"] == "active"
        assert row["latest_invoice_id"] == "in_current"
        assert row["last_stripe_event_id"] is None
        event_row = conn.execute(
            """
            SELECT processed_at IS NOT NULL AS processed
            FROM stripe_webhook_events
            WHERE stripe_event_id = 'evt_wrong_subscription_invoice_1'
            """
        ).fetchone()
        assert event_row["processed"] is True
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id = "
            "'evt_wrong_subscription_invoice_1'"
        )


def test_process_webhook_events_are_idempotent_and_update_subscription(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    now = int(time.time())
    checkout_event = {
        "id": "evt_checkout_1",
        "type": "checkout.session.completed",
        "livemode": False,
        "data": {
            "object": {
                "id": "cs_test_1",
                "customer": "cus_test_webhook",
                "subscription": "sub_test_1",
                "client_reference_id": tenant_id,
                "metadata": {"price_id": "price_test_monthly"},
                "customer_details": {"email": "ops@example.com"},
            }
        },
    }
    subscription_event = {
        "id": "evt_sub_1",
        "type": "customer.subscription.updated",
        "livemode": False,
        "data": {
            "object": {
                "id": "sub_test_1",
                "customer": "cus_test_webhook",
                "status": "active",
                "current_period_end": now,
                "cancel_at_period_end": False,
                "latest_invoice": "in_test_1",
                "items": {"data": [{"price": {"id": "price_test_monthly"}}]},
            }
        },
    }
    invoice_event = {
        "id": "evt_invoice_1",
        "type": "invoice.paid",
        "livemode": False,
        "data": {
            "object": {
                "id": "in_test_1",
                "customer": "cus_test_webhook",
                "subscription": "sub_test_1",
                "status": "paid",
                "amount_paid": 4900,
                "amount_due": 0,
                "currency": "usd",
                "hosted_invoice_url": "https://invoice.stripe.test/in_test_1",
                "invoice_pdf": "https://invoice.stripe.test/in_test_1.pdf",
            }
        },
    }
    unrelated_event = {
        "id": "evt_unrelated_1",
        "type": "invoice.payment_failed",
        "livemode": False,
        "data": {
            "object": {
                "id": "in_other_1",
                "customer": "cus_other_tenant",
                "subscription": "sub_other_1",
                "status": "open",
            }
        },
    }

    async def check() -> None:
        aconn = await _aconn_as(None)
        try:
            await aconn.execute("RESET ROLE")
            assert await billing.process_webhook_event(aconn, checkout_event) is True
            assert await billing.process_webhook_event(aconn, checkout_event) is False
            assert await billing.process_webhook_event(aconn, subscription_event) is True
            assert await billing.process_webhook_event(aconn, invoice_event) is True
            assert await billing.process_webhook_event(aconn, unrelated_event) is True
        finally:
            await aconn.close()

    async def check_graphql_events() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
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
                  billingEvents(limit: 10) {
                    stripeEventId
                    eventType
                    objectId
                    customerId
                    subscriptionId
                    objectStatus
                    latestInvoiceId
                    amountPaid
                    amountDue
                    amountTotal
                    currency
                    hostedInvoiceUrl
                    invoicePdf
                    processedAt
                    livemode
                  }
                }
                """,
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            events = {event["stripeEventId"]: event for event in result.data["billingEvents"]}
            assert set(events) == {"evt_checkout_1", "evt_sub_1", "evt_invoice_1"}
            assert events["evt_checkout_1"]["eventType"] == "checkout.session.completed"
            assert events["evt_checkout_1"]["objectId"] == "cs_test_1"
            assert events["evt_checkout_1"]["customerId"] == "cus_test_webhook"
            assert events["evt_sub_1"]["subscriptionId"] == "sub_test_1"
            assert events["evt_sub_1"]["objectStatus"] == "active"
            assert events["evt_invoice_1"]["eventType"] == "invoice.paid"
            assert events["evt_invoice_1"]["objectId"] == "in_test_1"
            assert events["evt_invoice_1"]["subscriptionId"] == "sub_test_1"
            assert events["evt_invoice_1"]["objectStatus"] == "paid"
            assert events["evt_invoice_1"]["amountPaid"] == 4900
            assert events["evt_invoice_1"]["amountDue"] == 0
            assert events["evt_invoice_1"]["amountTotal"] is None
            assert events["evt_invoice_1"]["currency"] == "usd"
            assert events["evt_invoice_1"]["hostedInvoiceUrl"] == (
                "https://invoice.stripe.test/in_test_1"
            )
            assert events["evt_invoice_1"]["invoicePdf"] == (
                "https://invoice.stripe.test/in_test_1.pdf"
            )
            assert events["evt_invoice_1"]["processedAt"] is not None
            assert events["evt_invoice_1"]["livemode"] is False
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
        row = conn.execute(
            """
            SELECT s.status, s.stripe_subscription_id, s.stripe_price_id, s.latest_invoice_id,
                   a.stripe_customer_id, a.billing_email
            FROM tenant_subscriptions s
            JOIN tenant_billing_accounts a ON a.tenant_id = s.tenant_id
            WHERE s.tenant_id = %s
            """,
            (tenant_id,),
        ).fetchone()
        assert row["status"] == "active"
        assert row["stripe_subscription_id"] == "sub_test_1"
        assert row["stripe_price_id"] == "price_test_monthly"
        assert row["latest_invoice_id"] == "in_test_1"
        assert row["stripe_customer_id"] == "cus_test_webhook"
        assert row["billing_email"] == "ops@example.com"
        events = conn.execute("SELECT count(*) AS n FROM stripe_webhook_events").fetchone()["n"]
        assert events >= 4
        asyncio.run(check_graphql_events())
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
