"""Tenant Stripe billing regression tests."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

import _itdb
from _mutations_common import WRITE_PRINCIPAL, _aconn_as
from fastapi.testclient import TestClient

from doc_ray.application import billing
from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema

_MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"


class FakeStripe:
    """In-memory Stripe transport for deterministic tests."""

    def __init__(self) -> None:
        self.created_customers: list[dict[str, Any]] = []
        self.checkout_calls: list[dict[str, Any]] = []
        self.portal_calls: list[dict[str, Any]] = []

    async def create_customer(
        self, *, name: str, email: str | None, metadata: dict[str, str]
    ) -> dict[str, Any]:
        self.created_customers.append({"name": name, "email": email, "metadata": metadata})
        return {"id": "cus_test_123"}

    async def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        client_reference_id: str,
        metadata: dict[str, str],
        subscription_metadata: dict[str, str],
    ) -> dict[str, Any]:
        self.checkout_calls.append(
            {
                "customer_id": customer_id,
                "price_id": price_id,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "client_reference_id": client_reference_id,
                "metadata": metadata,
                "subscription_metadata": subscription_metadata,
            }
        )
        return {"id": "cs_test_123", "url": "https://checkout.stripe.test/session"}

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> dict[str, Any]:
        self.portal_calls.append({"customer_id": customer_id, "return_url": return_url})
        return {"id": "bps_test_123", "url": "https://billing.stripe.test/session"}


def _cfg(*, enforcement_enabled: bool = False) -> Config:
    base = Config.from_env()
    return replace(
        base,
        database_url=_itdb.DB_URL,
        stripe_secret_key="sk_test_x",
        stripe_webhook_secret="whsec_test",
        stripe_price_id="price_test_monthly",
        stripe_success_url="https://app.example.com/billing/success",
        stripe_cancel_url="https://app.example.com/billing/cancel",
        stripe_portal_return_url="https://app.example.com/billing",
        public_base_url="https://app.example.com",
        billing_enforcement_enabled=enforcement_enabled,
    )


def _apply_billing_migration(conn) -> str:
    conn.execute((_MIGRATIONS / "080_billing.sql").read_text())
    conn.execute((_MIGRATIONS / "081_schema_version_1_25_0.sql").read_text())
    conn.execute((_MIGRATIONS / "083_billing_event_read.sql").read_text())
    conn.execute((_MIGRATIONS / "084_schema_version_1_26_0.sql").read_text())
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "billing-test-admin")
    return str(conn.execute("SELECT docray_default_tenant_id() AS id").fetchone()["id"])


def _signed_header(payload: bytes, secret: str, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.{payload.decode()}".encode(), sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_verify_stripe_signature_rejects_tampered_payload():
    payload = b'{"id":"evt_1","type":"checkout.session.completed"}'
    header = _signed_header(payload, "whsec_test", 1_700_000_000)

    event = billing.verify_stripe_signature(
        payload,
        header,
        "whsec_test",
        now=1_700_000_000,
    )
    assert event["id"] == "evt_1"

    tampered = b'{"id":"evt_2","type":"checkout.session.completed"}'
    try:
        billing.verify_stripe_signature(tampered, header, "whsec_test", now=1_700_000_000)
    except billing.StripeSignatureError as exc:
        assert "mismatch" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("tampered payload was accepted")


def test_stripe_webhook_route_verifies_signature_and_persists_event(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    event = {
        "id": "evt_route_checkout_1",
        "type": "checkout.session.completed",
        "livemode": False,
        "data": {
            "object": {
                "id": "cs_route_1",
                "customer": "cus_route_1",
                "subscription": "sub_route_1",
                "client_reference_id": tenant_id,
                "metadata": {"price_id": "price_test_monthly"},
                "customer_details": {"email": "route@example.com"},
            }
        },
    }
    payload = json.dumps(event, separators=(",", ":"), sort_keys=True).encode()
    header = _signed_header(payload, "whsec_test", int(time.time()))
    bad_header = _signed_header(b'{"id":"evt_route_checkout_1"}', "whsec_test", int(time.time()))
    app = create_app(_cfg())

    try:
        with TestClient(app) as client:
            rejected = client.post(
                "/stripe/webhook",
                content=payload,
                headers={"stripe-signature": bad_header},
            )
            assert rejected.status_code == 400
            accepted = client.post(
                "/stripe/webhook",
                content=payload,
                headers={"stripe-signature": header},
            )
            assert accepted.status_code == 200
            assert accepted.json() == {"received": True, "processed": True}
            duplicate = client.post(
                "/stripe/webhook",
                content=payload,
                headers={"stripe-signature": header},
            )
            assert duplicate.status_code == 200
            assert duplicate.json() == {"received": True, "processed": False}

        row = conn.execute(
            """
            SELECT s.status, s.checkout_session_id, s.stripe_subscription_id,
                   a.stripe_customer_id, a.billing_email
            FROM tenant_subscriptions s
            JOIN tenant_billing_accounts a ON a.tenant_id = s.tenant_id
            WHERE s.tenant_id = %s
            """,
            (tenant_id,),
        ).fetchone()
        assert row["status"] == "checkout_completed"
        assert row["checkout_session_id"] == "cs_route_1"
        assert row["stripe_subscription_id"] == "sub_route_1"
        assert row["stripe_customer_id"] == "cus_route_1"
        assert row["billing_email"] == "route@example.com"
        event_row = conn.execute(
            """
            SELECT processed_at IS NOT NULL AS processed
            FROM stripe_webhook_events
            WHERE stripe_event_id = 'evt_route_checkout_1'
            """
        ).fetchone()
        assert event_row["processed"] is True
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id = 'evt_route_checkout_1'"
        )


def test_billing_graphql_mutations_create_checkout_and_portal_sessions(pg, monkeypatch):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    stripe_calls: list[tuple[str, dict[str, str]]] = []

    async def fake_stripe_post(
        self: billing.StripeRestTransport, path: str, form: dict[str, str]
    ) -> dict[str, Any]:
        stripe_calls.append((path, form))
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
        assert customer_call == ("/customers", {
            "name": f"doc-ray tenant {tenant_id}",
            "metadata[tenant_id]": tenant_id,
        })
        assert checkout_call[0] == "/checkout/sessions"
        assert checkout_call[1]["mode"] == "subscription"
        assert checkout_call[1]["customer"] == "cus_graphql_1"
        assert checkout_call[1]["client_reference_id"] == tenant_id
        assert checkout_call[1]["subscription_data[metadata][tenant_id]"] == tenant_id
        assert checkout_call[1]["subscription_data[metadata][price_id]"] == "price_test_monthly"
        assert portal_call == (
            "/billing_portal/sessions",
            {"customer": "cus_graphql_1", "return_url": "https://app.example.com/billing"},
        )
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))


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
            assert fake.checkout_calls[0]["price_id"] == "price_test_monthly"
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
                    tenantName
                    stripeCustomerId
                    subscriptionStatus
                    checkoutReady
                    portalReady
                    webhookReady
                    webhookEndpointUrl
                    liveModeReady
                    missingConfiguration
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
            assert status["tenantName"] == "Current tenant"
            assert status["stripeCustomerId"] == "cus_test_123"
            assert status["subscriptionStatus"] == "none"
            assert status["checkoutReady"] is True
            assert status["portalReady"] is True
            assert status["webhookReady"] is True
            assert status["webhookEndpointUrl"] == "https://app.example.com/stripe/webhook"
            assert status["liveModeReady"] is False
            assert status["missingConfiguration"] == [
                "DOCRAY_BILLING_ENFORCEMENT_ENABLED=true"
            ]
            assert "checkout.session.completed" in status["requiredWebhookEvents"]
            assert "invoice.paid" in status["requiredWebhookEvents"]
            assert "invoice.finalization_failed" in status["requiredWebhookEvents"]
            assert status["enforcementEnabled"] is False
            assert status["entitled"] is True
            assert status["entitlementReason"] == "billing enforcement is disabled"
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))


def test_checkout_session_rejects_duplicate_active_subscription(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    fake = FakeStripe()
    conn.execute(
        """
        INSERT INTO tenant_subscriptions
            (tenant_id, stripe_customer_id, stripe_subscription_id, status)
        VALUES (%s, 'cus_active_existing', 'sub_active_existing', 'active')
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_subscription_id = EXCLUDED.stripe_subscription_id,
            status = EXCLUDED.status
        """,
        (tenant_id,),
    )

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            try:
                await billing.create_checkout_session(aconn, _cfg(), transport=fake)
            except billing.BillingConfigurationError as exc:
                assert "already has an active Stripe subscription" in str(exc)
            else:  # pragma: no cover - defensive
                raise AssertionError("active tenant was allowed to start duplicate Checkout")
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
        assert fake.created_customers == []
        assert fake.checkout_calls == []
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))


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
                (tenant_id, stripe_customer_id, stripe_subscription_id, status)
            VALUES (%s, 'cus_entitled', 'sub_entitled', 'active')
            ON CONFLICT (tenant_id) DO UPDATE
            SET stripe_customer_id = EXCLUDED.stripe_customer_id,
                stripe_subscription_id = EXCLUDED.stripe_subscription_id,
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
