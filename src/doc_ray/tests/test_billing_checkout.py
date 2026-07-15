"""Tenant Stripe billing regression tests — checkout."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from typing import Any

from _billing_test_support import (
    _apply_billing_migration,
    _cfg,
    _signed_header,
)
from _mutations_common import WRITE_PRINCIPAL, _aconn_as
from fastapi.testclient import TestClient

from doc_ray.application import billing
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema


def test_billing_graphql_mutations_create_checkout_and_portal_sessions(pg, monkeypatch):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    stripe_calls: list[tuple[str, dict[str, str], str | None]] = []

    async def fake_stripe_post(
        self: billing.StripeRestTransport,
        path: str,
        form: dict[str, str],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        stripe_calls.append((path, form, idempotency_key))
        if path == "/customers":
            return {"id": "cus_graphql_1"}
        if path == "/checkout/sessions":
            return {"id": "cs_graphql_1", "url": "https://checkout.stripe.test/graphql"}
        if path == "/billing_portal/sessions":
            return {"id": "bps_graphql_1", "url": "https://billing.stripe.test/graphql"}
        raise AssertionError(f"unexpected Stripe path {path}")

    monkeypatch.setattr(billing.StripeRestTransport, "_post", fake_stripe_post)
    cfg = replace(
        _cfg(),
        api_tokens={
            "billing-admin-token": (WRITE_PRINCIPAL.principal_id, frozenset({"read", "write"}))
        },
        api_allow_anonymous=False,
    )
    app = create_app(cfg)
    headers = {"authorization": "Bearer billing-admin-token"}

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
                headers=headers,
            )
            assert checkout.status_code == 200
            checkout_body = checkout.json()
            assert checkout_body.get("errors") is None
            assert checkout_body["data"]["createBillingCheckoutSession"]["url"] == (
                "https://checkout.stripe.test/graphql"
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
                headers=headers,
            )
            assert portal.status_code == 200
            portal_body = portal.json()
            assert portal_body.get("errors") is None
            assert portal_body["data"]["createBillingPortalSession"]["url"] == (
                "https://billing.stripe.test/graphql"
            )

        customer_call = stripe_calls[0]
        checkout_call = stripe_calls[1]
        portal_call = stripe_calls[2]
        assert customer_call == (
            "/customers",
            {
                "name": f"doc-ray tenant {tenant_id}",
                "metadata[tenant_id]": tenant_id,
            },
            f"docray.customer.{tenant_id}",
        )
        assert checkout_call[0] == "/checkout/sessions"
        assert checkout_call[1]["mode"] == "subscription"
        assert checkout_call[1]["customer"] == "cus_graphql_1"
        assert checkout_call[1]["client_reference_id"] == tenant_id
        assert checkout_call[1]["subscription_data[metadata][tenant_id]"] == tenant_id
        assert checkout_call[1]["subscription_data[metadata][price_id]"] == "price_test_monthly"
        assert checkout_call[2].startswith(f"docray.checkout.{tenant_id}.")
        assert portal_call == (
            "/billing_portal/sessions",
            {"customer": "cus_graphql_1", "return_url": "https://app.example.com/billing"},
            None,
        )
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))


def test_graphql_checkout_and_signed_webhooks_activate_billing_entitlement(pg, monkeypatch):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    stripe_calls: list[tuple[str, dict[str, str], str | None]] = []

    async def fake_stripe_post(
        self: billing.StripeRestTransport,
        path: str,
        form: dict[str, str],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        stripe_calls.append((path, form, idempotency_key))
        if path == "/customers":
            return {"id": "cus_e2e_1"}
        if path == "/checkout/sessions":
            return {"id": "cs_e2e_1", "url": "https://checkout.stripe.test/e2e"}
        raise AssertionError(f"unexpected Stripe path {path}")

    monkeypatch.setattr(billing.StripeRestTransport, "_post", fake_stripe_post)
    cfg = replace(
        _cfg(enforcement_enabled=True),
        stripe_secret_key="sk_live_realistic",
        api_tokens={
            "billing-admin-token": (WRITE_PRINCIPAL.principal_id, frozenset({"read", "write"}))
        },
        api_allow_anonymous=False,
    )
    app = create_app(cfg)
    headers = {"authorization": "Bearer billing-admin-token"}
    checkout_event = {
        "id": "evt_e2e_checkout_1",
        "type": "checkout.session.completed",
        "livemode": True,
        "data": {
            "object": {
                "id": "cs_e2e_1",
                "customer": "cus_e2e_1",
                "subscription": "sub_e2e_1",
                "client_reference_id": tenant_id,
                "metadata": {"price_id": "price_test_monthly"},
                "customer_details": {"email": "billing-e2e@example.com"},
            }
        },
    }
    subscription_event = {
        "id": "evt_e2e_subscription_1",
        "type": "customer.subscription.updated",
        "livemode": True,
        "data": {
            "object": {
                "id": "sub_e2e_1",
                "customer": "cus_e2e_1",
                "status": "active",
                "current_period_end": int(time.time()),
                "cancel_at_period_end": False,
                "latest_invoice": "in_e2e_1",
                "items": {"data": [{"price": {"id": "price_test_monthly"}}]},
                "metadata": {"tenant_id": tenant_id},
            }
        },
    }
    invoice_event = {
        "id": "evt_e2e_invoice_1",
        "type": "invoice.paid",
        "livemode": True,
        "data": {
            "object": {
                "id": "in_e2e_1",
                "customer": "cus_e2e_1",
                "subscription": "sub_e2e_1",
                "status": "paid",
                "amount_paid": 4900,
                "amount_due": 0,
                "currency": "usd",
                "hosted_invoice_url": "https://invoice.stripe.test/in_e2e_1",
                "invoice_pdf": "https://invoice.stripe.test/in_e2e_1.pdf",
            }
        },
    }

    def signed_event(event: dict[str, Any]) -> tuple[bytes, str]:
        payload = json.dumps(event, separators=(",", ":"), sort_keys=True).encode()
        return payload, _signed_header(payload, "whsec_test", int(time.time()))

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
                headers=headers,
            )
            assert checkout.status_code == 200
            checkout_body = checkout.json()
            assert checkout_body.get("errors") is None
            assert checkout_body["data"]["createBillingCheckoutSession"]["url"] == (
                "https://checkout.stripe.test/e2e"
            )

            pending = conn.execute(
                """
                SELECT status, checkout_session_id
                FROM tenant_subscriptions
                WHERE tenant_id = %s
                """,
                (tenant_id,),
            ).fetchone()
            assert pending["status"] == "checkout_pending"
            assert pending["checkout_session_id"] == "cs_e2e_1"

            for event in (checkout_event, subscription_event, invoice_event):
                payload, signature = signed_event(event)
                response = client.post(
                    "/stripe/webhook",
                    content=payload,
                    headers={"stripe-signature": signature},
                )
                assert response.status_code == 200
                assert response.json() == {"received": True, "processed": True}

            status_response = client.post(
                "/graphql",
                json={
                    "query": """
                    query {
                      billingStatus {
                        subscriptionStatus
                        stripeCustomerId
                        stripeSubscriptionId
                        stripePriceId
                        latestInvoiceId
                        enforcementEnabled
                        entitled
                        entitlementReason
                      }
                      billingEvents(limit: 5) {
                        stripeEventId
                        eventType
                        objectStatus
                        amountPaid
                        hostedInvoiceUrl
                        processedAt
                      }
                    }
                    """
                },
                headers=headers,
            )
            assert status_response.status_code == 200
            status_body = status_response.json()
            assert status_body.get("errors") is None
            assert status_body["data"]["billingStatus"] == {
                "subscriptionStatus": "active",
                "stripeCustomerId": "cus_e2e_1",
                "stripeSubscriptionId": "sub_e2e_1",
                "stripePriceId": "price_test_monthly",
                "latestInvoiceId": "in_e2e_1",
                "enforcementEnabled": True,
                "entitled": True,
                "entitlementReason": "subscription is active",
            }
            events = {
                event["stripeEventId"]: event for event in status_body["data"]["billingEvents"]
            }
            assert events["evt_e2e_invoice_1"]["eventType"] == "invoice.paid"
            assert events["evt_e2e_invoice_1"]["objectStatus"] == "paid"
            assert events["evt_e2e_invoice_1"]["amountPaid"] == 4900
            assert events["evt_e2e_invoice_1"]["hostedInvoiceUrl"] == (
                "https://invoice.stripe.test/in_e2e_1"
            )
            assert events["evt_e2e_invoice_1"]["processedAt"] is not None

        assert stripe_calls[0][0] == "/customers"
        assert stripe_calls[1][0] == "/checkout/sessions"
        assert stripe_calls[1][1]["customer"] == "cus_e2e_1"
        assert stripe_calls[1][1]["client_reference_id"] == tenant_id
        assert stripe_calls[1][2].startswith(f"docray.checkout.{tenant_id}.")
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id IN "
            "('evt_e2e_checkout_1', 'evt_e2e_subscription_1', 'evt_e2e_invoice_1')"
        )


def test_checkout_session_prefills_local_admin_billing_email(pg, monkeypatch):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    stripe_calls: list[tuple[str, dict[str, str], str | None]] = []

    async def fake_stripe_post(
        self: billing.StripeRestTransport,
        path: str,
        form: dict[str, str],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        stripe_calls.append((path, form, idempotency_key))
        if path == "/customers":
            return {"id": "cus_local_admin_1"}
        if path == "/checkout/sessions":
            return {"id": "cs_local_admin_1", "url": "https://checkout.stripe.test/local"}
        raise AssertionError(f"unexpected Stripe path {path}")

    monkeypatch.setattr(billing.StripeRestTransport, "_post", fake_stripe_post)
    schema = build_schema()
    local_principal = replace(
        WRITE_PRINCIPAL,
        subject="local:Billing.Admin@Example.COM",
    )

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=local_principal,
                loaders=Loaders(aconn),
                settings=_cfg(),
            )
            result = await schema.execute(
                """
                mutation {
                  createBillingCheckoutSession {
                    url
                  }
                }
                """,
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["createBillingCheckoutSession"]["url"] == (
                "https://checkout.stripe.test/local"
            )
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
        assert stripe_calls[0] == (
            "/customers",
            {
                "name": f"doc-ray tenant {tenant_id}",
                "email": "billing.admin@example.com",
                "metadata[tenant_id]": tenant_id,
            },
            f"docray.customer.{tenant_id}",
        )
        account = conn.execute(
            """
            SELECT stripe_customer_id, billing_email, created_by_principal_id
            FROM tenant_billing_accounts
            WHERE tenant_id = %s
            """,
            (tenant_id,),
        ).fetchone()
        assert account["stripe_customer_id"] == "cus_local_admin_1"
        assert account["billing_email"] == "billing.admin@example.com"
        assert str(account["created_by_principal_id"]) == WRITE_PRINCIPAL.principal_id
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))


def test_billing_console_queries_allow_read_scoped_admin(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    cfg = replace(
        _cfg(enforcement_enabled=True),
        api_tokens={"billing-read-token": (WRITE_PRINCIPAL.principal_id, frozenset({"read"}))},
        api_allow_anonymous=False,
    )
    app = create_app(cfg)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/graphql",
                json={
                    "query": """
                    query {
                      billingStatus {
                        tenantId
                        subscriptionStatus
                        enforcementEnabled
                        entitled
                        entitlementReason
                      }
                      billingEvents(limit: 5) {
                        stripeEventId
                      }
                    }
                    """
                },
                headers={"authorization": "Bearer billing-read-token"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body.get("errors") is None
        assert body["data"]["billingStatus"] == {
            "tenantId": tenant_id,
            "subscriptionStatus": "none",
            "enforcementEnabled": True,
            "entitled": False,
            "entitlementReason": "subscription is none",
        }
        assert body["data"]["billingEvents"] == []
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
