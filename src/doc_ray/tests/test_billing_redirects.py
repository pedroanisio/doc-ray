"""Tenant Stripe billing regression tests — redirects."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from _billing_test_support import (
    FakeStripe,
    _apply_billing_migration,
    _cfg,
)
from _mutations_common import WRITE_PRINCIPAL, _aconn_as

from doc_ray.application import billing


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


def test_live_portal_session_requires_complete_live_readiness(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    fake = FakeStripe()
    cfg = replace(
        _cfg(enforcement_enabled=True),
        stripe_secret_key="sk_live_realistic",
        stripe_webhook_secret="",
    )
    conn.execute(
        """
        INSERT INTO tenant_billing_accounts
            (tenant_id, stripe_customer_id, billing_email, created_by_principal_id)
        VALUES (%s, 'cus_live_portal', 'billing@example.com', %s)
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
                assert "Stripe Customer Portal is not live-ready" in message
                assert "DOCRAY_STRIPE_WEBHOOK_SECRET" in message
            else:  # pragma: no cover - defensive
                raise AssertionError("live Customer Portal opened without webhook readiness")
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
        assert fake.portal_calls == []
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
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id = 'evt_checkout_expired_1'"
        )


def test_checkout_session_after_expiration_uses_fresh_idempotency_key(pg):
    conn, _created = pg
    tenant_id = _apply_billing_migration(conn)
    fake = FakeStripe()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        webhook_conn = await _aconn_as(None)
        try:
            first_url = await billing.create_checkout_session(aconn, _cfg(), transport=fake)
            assert first_url == "https://checkout.stripe.test/session"
            first_key = fake.checkout_calls[0]["idempotency_key"]

            await webhook_conn.execute("RESET ROLE")
            assert (
                await billing.process_webhook_event(
                    webhook_conn,
                    {
                        "id": "evt_checkout_expired_fresh_key_1",
                        "type": "checkout.session.expired",
                        "livemode": False,
                        "data": {
                            "object": {
                                "id": "cs_test_123",
                                "customer": "cus_test_123",
                                "client_reference_id": tenant_id,
                                "metadata": {"tenant_id": tenant_id},
                            }
                        },
                    },
                )
                is True
            )

            second_url = await billing.create_checkout_session(aconn, _cfg(), transport=fake)
            assert second_url == "https://checkout.stripe.test/session"
            second_key = fake.checkout_calls[1]["idempotency_key"]

            assert first_key.startswith(f"docray.checkout.{tenant_id}.")
            assert second_key.startswith(f"docray.checkout.{tenant_id}.")
            assert second_key != first_key
            assert len(fake.created_customers) == 1
        finally:
            await webhook_conn.close()
            await aconn.close()

    try:
        asyncio.run(check())
    finally:
        conn.execute("DELETE FROM tenant_subscriptions WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenant_billing_accounts WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            "DELETE FROM stripe_webhook_events WHERE stripe_event_id = "
            "'evt_checkout_expired_fresh_key_1'"
        )
