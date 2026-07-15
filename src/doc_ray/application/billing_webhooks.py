"""Stripe webhook verification and event processing — split from :mod:`billing` (D-015).

Signature verification plus the handlers that apply verified Stripe events to
billing snapshots. Depends on :mod:`billing_base` primitives and the billing
store; never imports the core flows, so :mod:`billing` can import it freely.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any

import psycopg

from doc_ray.adapters import billing_postgres as billing_store
from doc_ray.application.billing_base import (
    ACTIONABLE_INVOICE_EVENTS as _ACTIONABLE_INVOICE_EVENTS,
)
from doc_ray.application.billing_base import (
    FAILED_INVOICE_EVENTS as _FAILED_INVOICE_EVENTS,
)
from doc_ray.application.billing_base import (
    PAID_INVOICE_EVENTS as _PAID_INVOICE_EVENTS,
)
from doc_ray.application.billing_base import (
    BillingConfigurationError,
    StripeSignatureError,
    WebhookProcessingPending,
    _string,
)
from doc_ray.config import Config


def verify_stripe_signature(
    payload: bytes,
    signature_header: str,
    webhook_secret: str,
    *,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> dict[str, Any]:
    """Verify a Stripe webhook signature header and decode the event JSON."""
    timestamp: int | None = None
    signatures: list[str] = []
    for part in signature_header.split(","):
        key, _, value = part.partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise StripeSignatureError("Stripe signature timestamp is malformed") from exc
        elif key == "v1":
            signatures.append(value)
    if timestamp is None or not signatures:
        raise StripeSignatureError("Stripe signature header is missing t/v1")
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        raise StripeSignatureError("Stripe signature timestamp is outside tolerance")
    try:
        payload_text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StripeSignatureError("Stripe event payload is not valid UTF-8") from exc
    signed_payload = f"{timestamp}.{payload_text}".encode()
    expected = hmac.new(webhook_secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise StripeSignatureError("Stripe signature mismatch")
    try:
        event = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise StripeSignatureError("Stripe event payload is not valid JSON") from exc
    if not isinstance(event, dict):
        raise StripeSignatureError("Stripe event payload is not an object")
    return event


async def process_webhook_event(
    conn: psycopg.AsyncConnection[Any],
    event: dict[str, Any],
    cfg: Config | None = None,
) -> bool:
    """Process one verified Stripe event; return false when already seen.

    Raises:
        WebhookProcessingPending: The event depends on Stripe state that has not
            reached doc-ray yet and should be delivered again later.
    """
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    if not event_id or not event_type:
        raise ValueError("Stripe event is missing id or type")
    validate_webhook_event_mode(event, cfg)
    inserted = await billing_store.insert_webhook_event(
        conn,
        stripe_event_id=event_id,
        event_type=event_type,
        livemode=bool(event.get("livemode", False)),
        payload=event,
    )
    if not inserted:
        return False
    handled = await _handle_webhook_event(conn, event_id, event_type, event)
    if not handled:
        raise WebhookProcessingPending(
            "Stripe webhook is pending tenant mapping; retry delivery later"
        )
    await billing_store.mark_webhook_processed(conn, event_id)
    return True


async def _handle_webhook_event(
    conn: psycopg.AsyncConnection[Any],
    event_id: str,
    event_type: str,
    event: dict[str, Any],
) -> bool:
    """Apply an already accepted Stripe event payload to billing snapshots."""
    obj = event.get("data", {}).get("object", {})
    if not isinstance(obj, dict):
        obj = {}
    if event_type == "checkout.session.completed":
        await _process_checkout_completed(conn, event_id, obj)
        handled = True
    elif event_type == "checkout.session.expired":
        await _process_checkout_expired(conn, obj)
        handled = True
    elif event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        await _process_subscription_event(conn, event_id, obj)
        handled = True
    elif event_type in {
        "invoice.paid",
        "invoice.payment_succeeded",
        "invoice.payment_failed",
        "invoice.finalization_failed",
    }:
        handled = await _process_invoice_event(conn, event_id, event_type, obj)
    else:
        handled = True
    return handled


def validate_webhook_event_mode(event: dict[str, Any], cfg: Config | None) -> None:
    """Require Stripe webhook livemode to match the configured Stripe key mode."""
    expected = _expected_stripe_livemode(cfg)
    if expected is None:
        return
    event_livemode = bool(event.get("livemode", False))
    if event_livemode != expected:
        expected_label = "live" if expected else "test"
        received_label = "live" if event_livemode else "test"
        raise BillingConfigurationError(
            "Stripe webhook livemode mismatch: "
            f"configured for {expected_label} mode but received {received_label} event"
        )


def _expected_stripe_livemode(cfg: Config | None) -> bool | None:
    key = (cfg.stripe_secret_key if cfg is not None else None) or ""
    if key.startswith(("sk_live_", "rk_live_")):
        return True
    if key.startswith(("sk_test_", "rk_test_")):
        return False
    return None


async def _process_checkout_completed(
    conn: psycopg.AsyncConnection[Any], event_id: str, obj: dict[str, Any]
) -> None:
    tenant_id = _tenant_from_object(obj)
    customer_id = _string(obj.get("customer"))
    if not tenant_id or not customer_id:
        return
    subscription_id = _string(obj.get("subscription"))
    price_id = _string(obj.get("metadata", {}).get("price_id"))
    await billing_store.upsert_billing_account(
        conn,
        tenant_id=tenant_id,
        stripe_customer_id=customer_id,
        billing_email=_string(obj.get("customer_details", {}).get("email")),
        created_by_principal_id=None,
    )
    await billing_store.record_checkout_session(
        conn,
        tenant_id=tenant_id,
        stripe_customer_id=customer_id,
        checkout_session_id=str(obj.get("id") or ""),
        stripe_subscription_id=subscription_id,
        stripe_price_id=price_id,
        stripe_event_id=event_id,
    )


async def _process_checkout_expired(
    conn: psycopg.AsyncConnection[Any], obj: dict[str, Any]
) -> None:
    session_id = _string(obj.get("id"))
    if not session_id:
        return
    await billing_store.expire_checkout_session(
        conn,
        checkout_session_id=session_id,
        tenant_id=_tenant_from_object(obj),
    )


async def _process_subscription_event(
    conn: psycopg.AsyncConnection[Any], event_id: str, obj: dict[str, Any]
) -> None:
    customer_id = _string(obj.get("customer"))
    subscription_id = _string(obj.get("id"))
    if not customer_id or not subscription_id:
        return
    tenant_id = await billing_store.tenant_for_stripe_customer(conn, customer_id)
    if tenant_id is None:
        tenant_id = _tenant_from_object(obj)
    if tenant_id is None:
        return
    await billing_store.upsert_billing_account(
        conn,
        tenant_id=tenant_id,
        stripe_customer_id=customer_id,
        billing_email=None,
        created_by_principal_id=None,
    )
    await billing_store.upsert_subscription_snapshot(
        conn,
        tenant_id=tenant_id,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        status=str(obj.get("status") or "unknown"),
        stripe_price_id=_subscription_price_id(obj),
        current_period_end=_timestamp_to_datetime(obj.get("current_period_end")),
        cancel_at_period_end=bool(obj.get("cancel_at_period_end", False)),
        latest_invoice_id=_string(obj.get("latest_invoice")),
        stripe_event_id=event_id,
    )


async def _process_invoice_event(
    conn: psycopg.AsyncConnection[Any],
    event_id: str,
    event_type: str,
    obj: dict[str, Any],
) -> bool:
    customer_id = _string(obj.get("customer"))
    if not customer_id:
        return True
    tenant_id = await billing_store.tenant_for_stripe_customer(conn, customer_id)
    if tenant_id is None:
        return event_type not in _PAID_INVOICE_EVENTS
    invoice_subscription_id = _string(obj.get("subscription"))
    updated = await billing_store.update_invoice_snapshot(
        conn,
        stripe_customer_id=customer_id,
        stripe_subscription_id=invoice_subscription_id,
        latest_invoice_id=_string(obj.get("id")),
        fallback_status=_invoice_fallback_status(event_type),
        stripe_event_id=event_id,
    )
    if updated or event_type not in _ACTIONABLE_INVOICE_EVENTS:
        return True
    if not invoice_subscription_id:
        return True
    status = await billing_store.get_billing_status(conn, tenant_id)
    known_subscription_id = _string(status.get("stripe_subscription_id"))
    return bool(known_subscription_id and known_subscription_id != invoice_subscription_id)


def _invoice_fallback_status(event_type: str) -> str | None:
    if event_type in _FAILED_INVOICE_EVENTS:
        return "past_due"
    if event_type in _PAID_INVOICE_EVENTS:
        return "active"
    return None


def _tenant_from_object(obj: dict[str, Any]) -> str | None:
    metadata = obj.get("metadata", {})
    tenant_id = metadata.get("tenant_id") if isinstance(metadata, dict) else None
    return _string(obj.get("client_reference_id")) or _string(tenant_id)


def _subscription_price_id(obj: dict[str, Any]) -> str | None:
    items = obj.get("items", {}).get("data", [])
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if not isinstance(first, dict):
        return None
    return _string(first.get("price", {}).get("id"))


def _timestamp_to_datetime(value: object) -> datetime | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
