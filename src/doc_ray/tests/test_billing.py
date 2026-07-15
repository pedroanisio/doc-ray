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
    conn.execute((_MIGRATIONS / "085_tenant_catalog_client_select.sql").read_text())
    conn.execute((_MIGRATIONS / "086_schema_version_1_27_0.sql").read_text())
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "billing-test-admin")
    return str(conn.execute("SELECT docray_default_tenant_id() AS id").fetchone()["id"])


def _signed_header(payload: bytes, secret: str, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.{payload.decode()}".encode(), sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _prod_example_webhook_events() -> list[str]:
    text = (_MIGRATIONS.parents[1] / ".env.prod.example").read_text()
    marker = "subscribe it to:"
    start = text.index(marker) + len(marker)
    end = text.index("DOCRAY_STRIPE_SECRET_KEY", start)
    block = text[start:end]
    event_names: list[str] = []
    for raw in block.replace("\n", " ").split(","):
        event = raw.replace("#", "").replace("and ", "").strip().rstrip(".")
        if event:
            event_names.append(event)
    return event_names


def test_prod_env_example_webhook_events_match_runtime_requirements():
    assert _prod_example_webhook_events() == list(billing.REQUIRED_WEBHOOK_EVENTS)


def test_stripe_rest_transport_surfaces_sanitized_error_body(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 15.0

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            data: dict[str, str],
            headers: dict[str, str],
        ) -> billing.httpx.Response:
            assert url == "https://api.stripe.test/v1/checkout/sessions"
            assert data["mode"] == "subscription"
            assert headers["authorization"] == "Bearer sk_live_secret_value"
            return billing.httpx.Response(
                402,
                json={
                    "error": {
                        "type": "card_error",
                        "code": "card_declined",
                        "decline_code": "insufficient_funds",
                        "message": "Your card was declined.",
                    }
                },
                headers={"request-id": "req_test_123"},
            )

    monkeypatch.setattr(billing.httpx, "AsyncClient", FakeAsyncClient)
    transport = billing.StripeRestTransport(
        secret_key="sk_live_secret_value",
        api_base="https://api.stripe.test/v1",
    )

    async def check() -> None:
        try:
            await transport.create_checkout_session(
                customer_id="cus_test",
                price_id="price_test",
                success_url="https://admin.example.com/billing?checkout=success",
                cancel_url="https://admin.example.com/billing?checkout=cancel",
                client_reference_id="tenant-1",
                metadata={"tenant_id": "tenant-1"},
                subscription_metadata={"tenant_id": "tenant-1"},
            )
        except billing.StripeAPIError as exc:
            message = str(exc)
            assert exc.status_code == 402
            assert exc.stripe_type == "card_error"
            assert exc.stripe_code == "card_declined"
            assert exc.decline_code == "insufficient_funds"
            assert exc.request_id == "req_test_123"
            assert "HTTP 402" in message
            assert "card_declined" in message
            assert "insufficient_funds" in message
            assert "req_test_123" in message
            assert "Your card was declined." in message
            assert "sk_live_secret_value" not in message
        else:  # pragma: no cover - defensive
            raise AssertionError("Stripe API error body was not surfaced")

    asyncio.run(check())


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


def test_stripe_webhook_route_retries_unmatched_invoice_after_subscription_mapping(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    invoice_event = {
        "id": "evt_route_invoice_before_mapping_1",
        "type": "invoice.paid",
        "livemode": False,
        "data": {
            "object": {
                "id": "in_route_before_mapping",
                "customer": "cus_route_before_mapping",
                "subscription": "sub_route_before_mapping",
                "status": "paid",
            }
        },
    }
    subscription_event = {
        "id": "evt_route_sub_after_invoice_1",
        "type": "customer.subscription.created",
        "livemode": False,
        "data": {
            "object": {
                "id": "sub_route_before_mapping",
                "customer": "cus_route_before_mapping",
                "status": "past_due",
                "current_period_end": int(time.time()),
                "cancel_at_period_end": False,
                "latest_invoice": "in_route_prior_failed",
                "items": {"data": [{"price": {"id": "price_test_monthly"}}]},
                "metadata": {"tenant_id": tenant_id},
            }
        },
    }
    invoice_payload = json.dumps(invoice_event, separators=(",", ":"), sort_keys=True).encode()
    subscription_payload = json.dumps(
        subscription_event, separators=(",", ":"), sort_keys=True
    ).encode()
    invoice_header = _signed_header(invoice_payload, "whsec_test", int(time.time()))
    subscription_header = _signed_header(subscription_payload, "whsec_test", int(time.time()))
    app = create_app(_cfg())

    try:
        with TestClient(app) as client:
            pending = client.post(
                "/stripe/webhook",
                content=invoice_payload,
                headers={"stripe-signature": invoice_header},
            )
            assert pending.status_code == 503
            assert "retry" in pending.json()["detail"].lower()

            subscription = client.post(
                "/stripe/webhook",
                content=subscription_payload,
                headers={"stripe-signature": subscription_header},
            )
            assert subscription.status_code == 200
            assert subscription.json() == {"received": True, "processed": True}

            retried_invoice = client.post(
                "/stripe/webhook",
                content=invoice_payload,
                headers={"stripe-signature": invoice_header},
            )
            assert retried_invoice.status_code == 200
            assert retried_invoice.json() == {"received": True, "processed": True}

            duplicate_invoice = client.post(
                "/stripe/webhook",
                content=invoice_payload,
                headers={"stripe-signature": invoice_header},
            )
            assert duplicate_invoice.status_code == 200
            assert duplicate_invoice.json() == {"received": True, "processed": False}

        row = conn.execute(
            """
            SELECT status, latest_invoice_id, last_stripe_event_id
            FROM tenant_subscriptions
            WHERE tenant_id = %s
            """,
            (tenant_id,),
        ).fetchone()
        assert row["status"] == "active"
        assert row["latest_invoice_id"] == "in_route_before_mapping"
        assert row["last_stripe_event_id"] == "evt_route_invoice_before_mapping_1"
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id IN "
            "('evt_route_invoice_before_mapping_1', 'evt_route_sub_after_invoice_1')"
        )


def test_stripe_webhook_route_rejects_livemode_mismatch_before_processing(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    event = {
        "id": "evt_route_mode_mismatch_1",
        "type": "checkout.session.completed",
        "livemode": False,
        "data": {
            "object": {
                "id": "cs_mode_mismatch",
                "customer": "cus_mode_mismatch",
                "subscription": "sub_mode_mismatch",
                "client_reference_id": tenant_id,
                "metadata": {"price_id": "price_test_monthly"},
            }
        },
    }
    payload = json.dumps(event, separators=(",", ":"), sort_keys=True).encode()
    header = _signed_header(payload, "whsec_test", int(time.time()))
    app = create_app(replace(_cfg(), stripe_secret_key="sk_live_x"))

    try:
        with TestClient(app) as client:
            response = client.post(
                "/stripe/webhook",
                content=payload,
                headers={"stripe-signature": header},
            )
            assert response.status_code == 400
            assert "livemode mismatch" in response.json()["detail"]

        event_row = conn.execute(
            """
            SELECT stripe_event_id
            FROM stripe_webhook_events
            WHERE stripe_event_id = 'evt_route_mode_mismatch_1'
            """
        ).fetchone()
        subscription_row = conn.execute(
            """
            SELECT stripe_subscription_id
            FROM tenant_subscriptions
            WHERE tenant_id = %s
            """,
            (tenant_id,),
        ).fetchone()
        assert event_row is None
        assert subscription_row is None
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id = "
            "'evt_route_mode_mismatch_1'"
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
            assert status["missingConfiguration"] == [
                "DOCRAY_BILLING_ENFORCEMENT_ENABLED=true"
            ]
            assert status["configurationIssues"] == []
            assert "checkout.session.completed" in status["requiredWebhookEvents"]
            assert "checkout.session.expired" in status["requiredWebhookEvents"]
            assert "invoice.paid" in status["requiredWebhookEvents"]
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


def test_checkout_redirect_overrides_must_use_configured_https_origin():
    cfg = _cfg(enforcement_enabled=True)

    urls = billing.configured_checkout_urls(
        cfg,
        success_url="https://app.example.com/billing/complete",
        cancel_url="https://app.example.com/billing/cancelled",
    )
    assert urls.success_url == "https://app.example.com/billing/complete"
    assert urls.cancel_url == "https://app.example.com/billing/cancelled"

    try:
        billing.configured_checkout_urls(
            cfg,
            success_url="https://evil.example.com/billing/complete",
            cancel_url="https://app.example.com/billing/cancelled",
        )
    except billing.BillingConfigurationError as exc:
        assert "success_url must use the configured application origin" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("cross-origin Checkout success URL override was accepted")

    try:
        billing.configured_checkout_urls(
            cfg,
            success_url="http://app.example.com/billing/complete",
            cancel_url="https://app.example.com/billing/cancelled",
        )
    except billing.BillingConfigurationError as exc:
        assert "success_url must use https for live billing" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("insecure Checkout success URL override was accepted")


def test_portal_return_override_must_use_configured_https_origin():
    cfg = _cfg(enforcement_enabled=True)

    url = billing.configured_portal_url(
        cfg,
        return_url="https://app.example.com/billing/portal-return",
    )
    assert url.return_url == "https://app.example.com/billing/portal-return"

    try:
        billing.configured_portal_url(
            cfg,
            return_url="https://evil.example.com/billing/portal-return",
        )
    except billing.BillingConfigurationError as exc:
        assert "portal_return_url must use the configured application origin" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("cross-origin Portal return URL override was accepted")

    try:
        billing.configured_portal_url(
            cfg,
            return_url="http://app.example.com/billing/portal-return",
        )
    except billing.BillingConfigurationError as exc:
        assert "portal_return_url must use https for live billing" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("insecure Portal return URL override was accepted")


def test_hosted_redirect_origin_can_differ_from_public_webhook_origin():
    cfg = replace(
        _cfg(enforcement_enabled=True),
        public_base_url="https://doc-ray.com",
        stripe_success_url="https://admin.doc-ray.com/billing?checkout=success",
        stripe_cancel_url="https://admin.doc-ray.com/billing?checkout=cancel",
        stripe_portal_return_url="https://admin.doc-ray.com/billing",
    )

    checkout = billing.configured_checkout_urls(
        cfg,
        success_url="https://admin.doc-ray.com/billing?checkout=success",
        cancel_url="https://admin.doc-ray.com/billing?checkout=cancel",
    )
    portal = billing.configured_portal_url(
        cfg,
        return_url="https://admin.doc-ray.com/billing",
    )

    assert billing.webhook_endpoint_url(cfg) == "https://doc-ray.com/stripe/webhook"
    assert checkout.success_url == "https://admin.doc-ray.com/billing?checkout=success"
    assert checkout.cancel_url == "https://admin.doc-ray.com/billing?checkout=cancel"
    assert portal.return_url == "https://admin.doc-ray.com/billing"

    try:
        billing.configured_checkout_urls(
            cfg,
            success_url="https://evil.example.com/billing?checkout=success",
            cancel_url="https://admin.doc-ray.com/billing?checkout=cancel",
        )
    except billing.BillingConfigurationError as exc:
        assert "success_url must use the configured application origin" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("cross-origin admin-console Checkout URL was accepted")


def test_live_checkout_session_requires_complete_live_readiness(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    fake = FakeStripe()
    cfg = replace(
        _cfg(enforcement_enabled=True),
        stripe_secret_key="sk_live_realistic",
        stripe_webhook_secret="",
    )

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            try:
                await billing.create_checkout_session(aconn, cfg, transport=fake)
            except billing.BillingConfigurationError as exc:
                message = str(exc)
                assert "Stripe Checkout is not live-ready" in message
                assert "DOCRAY_STRIPE_WEBHOOK_SECRET" in message
            else:  # pragma: no cover - defensive
                raise AssertionError("live Checkout started without webhook readiness")
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
        assert fake.created_customers == []
        assert fake.checkout_calls == []
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


def test_checkout_session_rejects_duplicate_checkout_completed_state(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    fake = FakeStripe()
    conn.execute(
        """
        INSERT INTO tenant_subscriptions
            (tenant_id, stripe_customer_id, stripe_subscription_id, status, checkout_session_id)
        VALUES (%s, 'cus_checkout_done', 'sub_checkout_done',
                'checkout_completed', 'cs_checkout_done')
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_subscription_id = EXCLUDED.stripe_subscription_id,
            status = EXCLUDED.status,
            checkout_session_id = EXCLUDED.checkout_session_id
        """,
        (tenant_id,),
    )

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            try:
                await billing.create_checkout_session(aconn, _cfg(), transport=fake)
            except billing.BillingConfigurationError as exc:
                assert "checkout_completed Stripe subscription state" in str(exc)
            else:  # pragma: no cover - defensive
                raise AssertionError("checkout-completed tenant started duplicate Checkout")
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
        assert fake.created_customers == []
        assert fake.checkout_calls == []
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))


def test_checkout_session_expired_clears_pending_checkout_state(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    conn.execute(
        """
        INSERT INTO tenant_billing_accounts
            (tenant_id, stripe_customer_id, billing_email, created_by_principal_id)
        VALUES (%s, 'cus_pending_expired', 'pending@example.com', %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            billing_email = EXCLUDED.billing_email
        """,
        (tenant_id, WRITE_PRINCIPAL.principal_id),
    )
    conn.execute(
        """
        INSERT INTO tenant_subscriptions
            (tenant_id, stripe_customer_id, stripe_price_id, status, checkout_session_id)
        VALUES (%s, 'cus_pending_expired', 'price_test_monthly',
                'checkout_pending', 'cs_pending_expired')
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_price_id = EXCLUDED.stripe_price_id,
            status = EXCLUDED.status,
            checkout_session_id = EXCLUDED.checkout_session_id
        """,
        (tenant_id,),
    )
    event = {
        "id": "evt_checkout_expired_1",
        "type": "checkout.session.expired",
        "livemode": False,
        "data": {
            "object": {
                "id": "cs_pending_expired",
                "customer": "cus_pending_expired",
                "client_reference_id": tenant_id,
                "metadata": {"tenant_id": tenant_id},
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
            SELECT status, checkout_session_id
            FROM tenant_subscriptions
            WHERE tenant_id = %s
            """,
            (tenant_id,),
        ).fetchone()
        assert row["status"] == "none"
        assert row["checkout_session_id"] is None
        event_row = conn.execute(
            """
            SELECT processed_at IS NOT NULL AS processed
            FROM stripe_webhook_events
            WHERE stripe_event_id = 'evt_checkout_expired_1'
            """
        ).fetchone()
        assert event_row["processed"] is True
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM stripe_webhook_events WHERE stripe_event_id = 'evt_checkout_expired_1'")


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
                "entitlementReason": (
                    "subscription price does not match configured Stripe price"
                ),
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
        conn.execute("DELETE FROM stripe_webhook_events WHERE stripe_event_id = 'evt_retry_invoice_1'")


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
        conn.execute("DELETE FROM stripe_webhook_events WHERE stripe_event_id = 'evt_invoice_recovered_1'")


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
