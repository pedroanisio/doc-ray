"""Tenant Stripe billing regression tests — core."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import replace

from _billing_test_support import (
    _apply_billing_migration,
    _architecture_stripe_section,
    _cfg,
    _prod_example_webhook_events,
    _production_setup_webhook_events,
    _signed_header,
)
from fastapi.testclient import TestClient

from doc_ray.application import billing, billing_stripe
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.mutation_guards import _BILLING_ENTITLEMENT_EXEMPT_ACTIONS
from doc_ray.entrypoints.api.schema import build_schema


def test_paid_saas_mutations_cannot_drift_outside_billing_entitlement_guard() -> None:
    schema = build_schema()
    mutation_fields = {
        field.python_name: field.base_resolver.wrapped_func
        for field in schema.mutation.__strawberry_definition__.fields
    }
    exempt_mutations = {
        "create_billing_checkout_session": "mutation.create_billing_checkout_session",
        "create_billing_portal_session": "mutation.create_billing_portal_session",
        "retry_billing_event": "mutation.retry_billing_event",
    }
    guarded_mutations = {
        "add_document_to_collection": "mutation.add_document_to_collection",
        "annotate_document": "mutation.annotate",
        "archive_document": "mutation.archive_document",
        "bulk_archive_documents": "mutation.bulk_archive_documents",
        "bulk_tag_documents": "mutation.bulk_tag_documents",
        "cancel_job": "mutation.cancel_job",
        "canonicalize_document": "mutation.canonicalize",
        "correct_entity_mention": "mutation.correct_entity_mention",
        "create_backup": "mutation.backup",
        "create_collection": "mutation.create_collection",
        "decide_document_metadata_suggestions": ("mutation.decide_document_metadata_suggestions"),
        "decide_seeds": "mutation.decide_seeds",
        "delete_collection": "mutation.delete_collection",
        "delete_document": "mutation.delete",
        "demote_lens": "mutation.demote_lens",
        "embed_document_sentences": "mutation.embed_document_sentences",
        "ingest_document": "mutation.ingest",
        "promote_collection_to_lens": "mutation.promote_collection_to_lens",
        "reannotate": "mutation.reannotate",
        "reextract_document": "mutation.reextract",
        "reextract_documents": "mutation.reextract_documents",
        "remove_document_from_collection": "mutation.remove_document_from_collection",
        "restore_backup": "mutation.restore",
        "restore_document": "mutation.restore_document",
        "revoke_document_share": "mutation.revoke_document_share",
        "seed_approved": "mutation.seed_approved",
        "set_document_default_lens": "mutation.set_document_default_lens",
        "semantic_enrich_document": "mutation.semantic_enrich",
        "set_document_visibility": "mutation.set_document_visibility",
        "share_document": "mutation.share_document",
        "suggest_document_metadata": "mutation.suggest_document_metadata",
        "suggest_seeds": "mutation.seed_suggest",
        "update_collection": "mutation.update_collection",
        "update_document_metadata": "mutation.update_metadata",
    }
    assert set(mutation_fields) == set(guarded_mutations) | set(exempt_mutations)
    assert set(exempt_mutations.values()) == _BILLING_ENTITLEMENT_EXEMPT_ACTIONS
    for mutation_name, action in guarded_mutations.items():
        source = inspect.getsource(mutation_fields[mutation_name])
        assert action in source
        assert any(
            guard in source
            for guard in (
                "_require_admin",
                "_require_document_write",
                "_require_document_share",
                "_require_write_audited",
            )
        ), mutation_name


def test_prod_env_example_webhook_events_match_runtime_requirements():
    assert _prod_example_webhook_events() == list(billing.REQUIRED_WEBHOOK_EVENTS)


def test_production_setup_webhook_events_match_runtime_requirements():
    assert _production_setup_webhook_events() == list(billing.REQUIRED_WEBHOOK_EVENTS)


def test_architecture_documents_stripe_payment_control_plane():
    section = _architecture_stripe_section()
    for event_name in billing.REQUIRED_WEBHOOK_EVENTS:
        assert f"`{event_name}`" in section
    for fragment in (
        "createBillingCheckoutSession",
        "createBillingPortalSession",
        "retryBillingEvent",
        "billingStatus",
        "billingEvents",
        "POST /stripe/webhook",
        "DOCRAY_STRIPE_WEBHOOK_SECRET",
        "DOCRAY_STRIPE_SECRET_KEY",
        "DOCRAY_BILLING_ENFORCEMENT_ENABLED=true",
        "DOCRAY_STRIPE_PRICE_ID",
        "DOCRAY_PUBLIC_BASE_URL",
        "hosted Checkout and Customer Portal are refused until signed webhook",
        "tenant_billing_accounts",
        "tenant_subscriptions",
        "stripe_webhook_events",
        "admin.doc-ray.com",
    ):
        assert fragment in section


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
        ) -> billing_stripe.httpx.Response:
            assert url == "https://api.stripe.test/v1/checkout/sessions"
            assert data["mode"] == "subscription"
            assert headers["authorization"] == "Bearer sk_live_secret_value"
            assert headers["idempotency-key"] == "docray.checkout.tenant-1.price_test"
            return billing_stripe.httpx.Response(
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

    monkeypatch.setattr(billing_stripe.httpx, "AsyncClient", FakeAsyncClient)
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
                idempotency_key="docray.checkout.tenant-1.price_test",
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


def test_verify_stripe_signature_rejects_malformed_timestamp():
    payload = b'{"id":"evt_1","type":"checkout.session.completed"}'
    try:
        billing.verify_stripe_signature(
            payload,
            "t=not-a-timestamp,v1=abc123",
            "whsec_test",
            now=1_700_000_000,
        )
    except billing.StripeSignatureError as exc:
        assert "timestamp" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("malformed Stripe signature timestamp was accepted")


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
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id = 'evt_route_mode_mismatch_1'"
        )
