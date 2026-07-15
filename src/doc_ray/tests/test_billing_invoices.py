"""Tenant Stripe billing regression tests — invoices."""

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


def test_out_of_order_invoice_without_customer_mapping_retries_after_subscription(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    invoice_event = {
        "id": "evt_invoice_before_mapping_1",
        "type": "invoice.paid",
        "livemode": False,
        "data": {
            "object": {
                "id": "in_before_mapping",
                "customer": "cus_before_mapping",
                "subscription": "sub_before_mapping",
                "status": "paid",
            }
        },
    }
    subscription_event = {
        "id": "evt_sub_after_invoice_1",
        "type": "customer.subscription.created",
        "livemode": False,
        "data": {
            "object": {
                "id": "sub_before_mapping",
                "customer": "cus_before_mapping",
                "status": "past_due",
                "current_period_end": int(time.time()),
                "cancel_at_period_end": False,
                "latest_invoice": "in_prior_failed",
                "items": {"data": [{"price": {"id": "price_test_monthly"}}]},
                "metadata": {"tenant_id": tenant_id},
            }
        },
    }

    async def process() -> None:
        aconn = await _aconn_as(None)
        try:
            await aconn.execute("RESET ROLE")
            try:
                await billing.process_webhook_event(aconn, invoice_event)
            except billing.WebhookProcessingPending as exc:
                assert "retry" in str(exc).lower()
            else:  # pragma: no cover - defensive
                raise AssertionError("unmatched invoice was marked processed")
            assert await billing.process_webhook_event(aconn, subscription_event) is True
            assert await billing.process_webhook_event(aconn, invoice_event) is True
            assert await billing.process_webhook_event(aconn, invoice_event) is False
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
        assert row["status"] == "active"
        assert row["latest_invoice_id"] == "in_before_mapping"
        assert row["last_stripe_event_id"] == "evt_invoice_before_mapping_1"
        invoice_row = conn.execute(
            """
            SELECT processed_at IS NOT NULL AS processed
            FROM stripe_webhook_events
            WHERE stripe_event_id = 'evt_invoice_before_mapping_1'
            """
        ).fetchone()
        assert invoice_row["processed"] is True
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id IN "
            "('evt_invoice_before_mapping_1', 'evt_sub_after_invoice_1')"
        )


def test_paid_invoice_for_known_customer_retries_until_subscription_snapshot_exists(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    conn.execute(
        """
        INSERT INTO tenant_billing_accounts
            (tenant_id, stripe_customer_id, billing_email, created_by_principal_id)
        VALUES (%s, 'cus_known_before_subscription', 'known@example.com', %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            billing_email = EXCLUDED.billing_email
        """,
        (tenant_id, WRITE_PRINCIPAL.principal_id),
    )
    invoice_event = {
        "id": "evt_invoice_known_customer_before_subscription_1",
        "type": "invoice.paid",
        "livemode": False,
        "data": {
            "object": {
                "id": "in_known_before_subscription",
                "customer": "cus_known_before_subscription",
                "subscription": "sub_known_before_subscription",
                "status": "paid",
            }
        },
    }
    subscription_event = {
        "id": "evt_sub_known_customer_after_invoice_1",
        "type": "customer.subscription.created",
        "livemode": False,
        "data": {
            "object": {
                "id": "sub_known_before_subscription",
                "customer": "cus_known_before_subscription",
                "status": "past_due",
                "current_period_end": int(time.time()),
                "cancel_at_period_end": False,
                "latest_invoice": "in_prior_known_failed",
                "items": {"data": [{"price": {"id": "price_test_monthly"}}]},
                "metadata": {"tenant_id": tenant_id},
            }
        },
    }

    async def process() -> None:
        aconn = await _aconn_as(None)
        try:
            await aconn.execute("RESET ROLE")
            try:
                await billing.process_webhook_event(aconn, invoice_event)
            except billing.WebhookProcessingPending as exc:
                assert "retry" in str(exc).lower()
            else:  # pragma: no cover - defensive
                raise AssertionError("invoice with no subscription snapshot was marked processed")
            assert await billing.process_webhook_event(aconn, subscription_event) is True
            assert await billing.process_webhook_event(aconn, invoice_event) is True
            assert await billing.process_webhook_event(aconn, invoice_event) is False
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
        assert row["status"] == "active"
        assert row["latest_invoice_id"] == "in_known_before_subscription"
        assert row["last_stripe_event_id"] == "evt_invoice_known_customer_before_subscription_1"
        invoice_row = conn.execute(
            """
            SELECT processed_at IS NOT NULL AS processed
            FROM stripe_webhook_events
            WHERE stripe_event_id = 'evt_invoice_known_customer_before_subscription_1'
            """
        ).fetchone()
        assert invoice_row["processed"] is True
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id IN "
            "('evt_invoice_known_customer_before_subscription_1', "
            "'evt_sub_known_customer_after_invoice_1')"
        )


def test_failed_invoice_for_known_customer_retries_until_subscription_snapshot_exists(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    conn.execute(
        """
        INSERT INTO tenant_billing_accounts
            (tenant_id, stripe_customer_id, billing_email, created_by_principal_id)
        VALUES (%s, 'cus_failed_before_subscription', 'known-failed@example.com', %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            billing_email = EXCLUDED.billing_email
        """,
        (tenant_id, WRITE_PRINCIPAL.principal_id),
    )
    invoice_event = {
        "id": "evt_invoice_failed_customer_before_subscription_1",
        "type": "invoice.payment_failed",
        "livemode": False,
        "data": {
            "object": {
                "id": "in_failed_before_subscription",
                "customer": "cus_failed_before_subscription",
                "subscription": "sub_failed_before_subscription",
                "status": "open",
            }
        },
    }
    subscription_event = {
        "id": "evt_sub_failed_customer_after_invoice_1",
        "type": "customer.subscription.created",
        "livemode": False,
        "data": {
            "object": {
                "id": "sub_failed_before_subscription",
                "customer": "cus_failed_before_subscription",
                "status": "active",
                "current_period_end": int(time.time()),
                "cancel_at_period_end": False,
                "latest_invoice": "in_prior_active",
                "items": {"data": [{"price": {"id": "price_test_monthly"}}]},
                "metadata": {"tenant_id": tenant_id},
            }
        },
    }

    async def process() -> None:
        aconn = await _aconn_as(None)
        try:
            await aconn.execute("RESET ROLE")
            try:
                await billing.process_webhook_event(aconn, invoice_event)
            except billing.WebhookProcessingPending as exc:
                assert "retry" in str(exc).lower()
            else:  # pragma: no cover - defensive
                raise AssertionError("failed invoice with no subscription snapshot was processed")
            assert await billing.process_webhook_event(aconn, subscription_event) is True
            assert await billing.process_webhook_event(aconn, invoice_event) is True
            assert await billing.process_webhook_event(aconn, invoice_event) is False
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
        assert row["status"] == "past_due"
        assert row["latest_invoice_id"] == "in_failed_before_subscription"
        assert row["last_stripe_event_id"] == "evt_invoice_failed_customer_before_subscription_1"
        invoice_row = conn.execute(
            """
            SELECT processed_at IS NOT NULL AS processed
            FROM stripe_webhook_events
            WHERE stripe_event_id = 'evt_invoice_failed_customer_before_subscription_1'
            """
        ).fetchone()
        assert invoice_row["processed"] is True
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id IN "
            "('evt_invoice_failed_customer_before_subscription_1', "
            "'evt_sub_failed_customer_after_invoice_1')"
        )


def test_paid_invoice_restores_past_due_billing_entitlement(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    schema = build_schema()
    conn.execute(
        """
        INSERT INTO tenant_billing_accounts
            (tenant_id, stripe_customer_id, billing_email, created_by_principal_id)
        VALUES (%s, 'cus_recovered_invoice', 'recovered@example.com', %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            billing_email = EXCLUDED.billing_email
        """,
        (tenant_id, WRITE_PRINCIPAL.principal_id),
    )
    conn.execute(
        """
        INSERT INTO tenant_subscriptions
            (tenant_id, stripe_customer_id, stripe_subscription_id, stripe_price_id,
             status, latest_invoice_id)
        VALUES (%s, 'cus_recovered_invoice', 'sub_recovered_invoice',
                'price_test_monthly', 'past_due', 'in_failed_prior')
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_subscription_id = EXCLUDED.stripe_subscription_id,
            stripe_price_id = EXCLUDED.stripe_price_id,
            status = EXCLUDED.status,
            latest_invoice_id = EXCLUDED.latest_invoice_id
        """,
        (tenant_id,),
    )
    event = {
        "id": "evt_invoice_recovered_1",
        "type": "invoice.paid",
        "livemode": False,
        "data": {
            "object": {
                "id": "in_recovered",
                "customer": "cus_recovered_invoice",
                "subscription": "sub_recovered_invoice",
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
                "subscriptionStatus": "active",
                "latestInvoiceId": "in_recovered",
                "entitled": True,
                "entitlementReason": "subscription is active",
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
        assert row["status"] == "active"
        assert row["latest_invoice_id"] == "in_recovered"
        assert row["last_stripe_event_id"] == "evt_invoice_recovered_1"
        asyncio.run(check_status())
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id = 'evt_invoice_recovered_1'"
        )
