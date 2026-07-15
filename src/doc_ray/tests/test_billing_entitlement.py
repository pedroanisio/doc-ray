"""Tenant Stripe billing regression tests — entitlement."""

from __future__ import annotations

import asyncio
import json
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


def test_subscription_webhook_before_checkout_creates_customer_mapping(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    now = int(time.time())
    subscription_event = {
        "id": "evt_sub_out_of_order_1",
        "type": "customer.subscription.created",
        "livemode": False,
        "data": {
            "object": {
                "id": "sub_out_of_order",
                "customer": "cus_out_of_order",
                "status": "active",
                "current_period_end": now,
                "cancel_at_period_end": False,
                "latest_invoice": "in_out_of_order",
                "items": {"data": [{"price": {"id": "price_test_monthly"}}]},
                "metadata": {"tenant_id": tenant_id},
            }
        },
    }

    async def check() -> None:
        aconn = await _aconn_as(None)
        try:
            await aconn.execute("RESET ROLE")
            assert await billing.process_webhook_event(aconn, subscription_event) is True
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
        row = conn.execute(
            """
            SELECT s.status, s.stripe_subscription_id, s.stripe_price_id,
                   a.stripe_customer_id
            FROM tenant_subscriptions s
            JOIN tenant_billing_accounts a ON a.tenant_id = s.tenant_id
            WHERE s.tenant_id = %s
            """,
            (tenant_id,),
        ).fetchone()
        assert row["status"] == "active"
        assert row["stripe_subscription_id"] == "sub_out_of_order"
        assert row["stripe_price_id"] == "price_test_monthly"
        assert row["stripe_customer_id"] == "cus_out_of_order"
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id = 'evt_sub_out_of_order_1'"
        )


def test_paid_mutations_require_entitled_subscription_when_enforced(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    schema = build_schema()

    async def check_blocked() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=WRITE_PRINCIPAL,
                loaders=Loaders(aconn),
                settings=_cfg(enforcement_enabled=True),
            )
            result = await schema.execute(
                'mutation { createBackup(note: "billing-blocked") { id } }',
                context_value=ctx,
            )
            assert result.errors is not None
            assert "billing subscription required" in str(result.errors[0]).lower()
        finally:
            await aconn.close()

    async def check_allowed() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=WRITE_PRINCIPAL,
                loaders=Loaders(aconn),
                settings=_cfg(enforcement_enabled=True),
            )
            result = await schema.execute(
                'mutation { createBackup(note: "billing-allowed") { kind status } }',
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["createBackup"]["status"] == "queued"
        finally:
            await aconn.close()

    try:
        asyncio.run(check_blocked())
        conn.execute(
            """
            INSERT INTO tenant_subscriptions
                (tenant_id, stripe_customer_id, stripe_subscription_id, stripe_price_id, status)
            VALUES (%s, 'cus_entitled', 'sub_entitled', 'price_test_monthly', 'active')
            ON CONFLICT (tenant_id) DO UPDATE
            SET stripe_customer_id = EXCLUDED.stripe_customer_id,
                stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                stripe_price_id = EXCLUDED.stripe_price_id,
                status = EXCLUDED.status
            """,
            (tenant_id,),
        )
        asyncio.run(check_allowed())
    finally:
        conn.execute(
            "DELETE FROM jobs WHERE kind = 'backup' AND payload->>'note' IN "
            "('billing-blocked', 'billing-allowed')"
        )
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))


def test_billing_console_queries_remain_available_when_unpaid_and_enforced(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    schema = build_schema()

    async def check() -> None:
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
                    tenantId
                    subscriptionStatus
                    enforcementEnabled
                    entitled
                    entitlementReason
                  }
                  billingEvents(limit: 10) {
                    stripeEventId
                  }
                }
                """,
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["billingStatus"] == {
                "tenantId": tenant_id,
                "subscriptionStatus": "none",
                "enforcementEnabled": True,
                "entitled": False,
                "entitlementReason": "subscription is none",
            }
            assert result.data["billingEvents"] == []
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))


def test_active_subscription_with_wrong_price_is_not_entitled(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    schema = build_schema()
    conn.execute(
        """
        INSERT INTO tenant_subscriptions
            (tenant_id, stripe_customer_id, stripe_subscription_id, stripe_price_id, status)
        VALUES (%s, 'cus_wrong_price', 'sub_wrong_price', 'price_legacy_plan', 'active')
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_subscription_id = EXCLUDED.stripe_subscription_id,
            stripe_price_id = EXCLUDED.stripe_price_id,
            status = EXCLUDED.status
        """,
        (tenant_id,),
    )

    async def check() -> None:
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
                    stripePriceId
                    enforcementEnabled
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
                "stripePriceId": "price_legacy_plan",
                "enforcementEnabled": True,
                "entitled": False,
                "entitlementReason": ("subscription price does not match configured Stripe price"),
            }
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))


def test_invoice_finalization_failed_removes_billing_entitlement(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    schema = build_schema()
    conn.execute(
        """
        INSERT INTO tenant_billing_accounts
            (tenant_id, stripe_customer_id, billing_email, created_by_principal_id)
        VALUES (%s, 'cus_finalization_failed', 'billing@example.com', %s)
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
        VALUES (%s, 'cus_finalization_failed', 'sub_finalization_failed', 'active', 'in_prior')
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_subscription_id = EXCLUDED.stripe_subscription_id,
            status = EXCLUDED.status,
            latest_invoice_id = EXCLUDED.latest_invoice_id
        """,
        (tenant_id,),
    )
    event = {
        "id": "evt_invoice_finalization_failed_1",
        "type": "invoice.finalization_failed",
        "livemode": False,
        "data": {
            "object": {
                "id": "in_finalization_failed",
                "customer": "cus_finalization_failed",
                "subscription": "sub_finalization_failed",
                "status": "draft",
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
                  billingEvents(limit: 5) {
                    stripeEventId
                    eventType
                    objectStatus
                  }
                }
                """,
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["billingStatus"] == {
                "subscriptionStatus": "past_due",
                "latestInvoiceId": "in_finalization_failed",
                "entitled": False,
                "entitlementReason": "subscription is past_due",
            }
            events = {event["stripeEventId"]: event for event in result.data["billingEvents"]}
            assert events["evt_invoice_finalization_failed_1"] == {
                "stripeEventId": "evt_invoice_finalization_failed_1",
                "eventType": "invoice.finalization_failed",
                "objectStatus": "draft",
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
        assert row["status"] == "past_due"
        assert row["latest_invoice_id"] == "in_finalization_failed"
        assert row["last_stripe_event_id"] == "evt_invoice_finalization_failed_1"
        asyncio.run(check_status())
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id = "
            "'evt_invoice_finalization_failed_1'"
        )


def test_unprocessed_webhook_event_can_be_retried(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    event = {
        "id": "evt_retry_invoice_1",
        "type": "invoice.paid",
        "livemode": False,
        "data": {
            "object": {
                "id": "in_retry_paid",
                "customer": "cus_retry_webhook",
                "subscription": "sub_retry_webhook",
                "status": "paid",
            }
        },
    }
    conn.execute(
        """
        INSERT INTO tenant_billing_accounts
            (tenant_id, stripe_customer_id, billing_email, created_by_principal_id)
        VALUES (%s, 'cus_retry_webhook', 'retry@example.com', %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            billing_email = EXCLUDED.billing_email
        """,
        (tenant_id, WRITE_PRINCIPAL.principal_id),
    )
    conn.execute(
        """
        INSERT INTO tenant_subscriptions
            (tenant_id, stripe_customer_id, stripe_subscription_id, status)
        VALUES (%s, 'cus_retry_webhook', 'sub_retry_webhook', 'active')
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_subscription_id = EXCLUDED.stripe_subscription_id,
            status = EXCLUDED.status
        """,
        (tenant_id,),
    )
    conn.execute(
        """
        INSERT INTO stripe_webhook_events
            (stripe_event_id, event_type, livemode, payload, processed_at)
        VALUES (%s, %s, false, %s::jsonb, NULL)
        ON CONFLICT (stripe_event_id) DO UPDATE
        SET processed_at = NULL,
            payload = EXCLUDED.payload
        """,
        (event["id"], event["type"], json.dumps(event)),
    )

    async def check() -> None:
        aconn = await _aconn_as(None)
        try:
            await aconn.execute("RESET ROLE")
            assert await billing.process_webhook_event(aconn, event) is True
            assert await billing.process_webhook_event(aconn, event) is False
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
        assert row["latest_invoice_id"] == "in_retry_paid"
        assert row["last_stripe_event_id"] == "evt_retry_invoice_1"
        event_row = conn.execute(
            """
            SELECT processed_at IS NOT NULL AS processed
            FROM stripe_webhook_events
            WHERE stripe_event_id = 'evt_retry_invoice_1'
            """
        ).fetchone()
        assert event_row["processed"] is True
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id = 'evt_retry_invoice_1'"
        )
