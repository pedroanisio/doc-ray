"""Stripe billing orchestration for tenant subscriptions."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx
import psycopg

from doc_ray.adapters import billing_postgres as billing_store
from doc_ray.config import Config


class BillingConfigurationError(RuntimeError):
    """Raised when a billing action needs missing Stripe configuration."""


class BillingEntitlementError(PermissionError):
    """Raised when a tenant is not entitled to paid SaaS actions."""


class StripeSignatureError(ValueError):
    """Raised when a Stripe webhook signature cannot be verified."""


ENTITLED_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing"})


@dataclass(frozen=True)
class BillingUrls:
    """Redirect URLs used by hosted Stripe flows."""

    success_url: str
    cancel_url: str


@dataclass(frozen=True)
class PortalUrl:
    """Redirect URL used by Stripe Customer Portal."""

    return_url: str


class StripeTransport(Protocol):
    """Minimal Stripe API surface required by doc-ray billing."""

    async def create_customer(
        self, *, name: str, email: str | None, metadata: dict[str, str]
    ) -> dict[str, Any]:
        """Create a Stripe customer."""

    async def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        client_reference_id: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        """Create a hosted subscription Checkout Session."""

    async def create_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> dict[str, Any]:
        """Create a hosted Customer Portal Session."""


class StripeRestTransport:
    """Small httpx-based Stripe REST client for hosted billing flows."""

    def __init__(self, *, secret_key: str, api_base: str = "https://api.stripe.com/v1") -> None:
        """Initialize the transport with Stripe credentials."""
        self._secret_key = secret_key
        self._api_base = api_base.rstrip("/")

    async def _post(self, path: str, form: dict[str, str]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{self._api_base}{path}",
                data=form,
                headers={"authorization": f"Bearer {self._secret_key}"},
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Stripe request failed with HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Stripe response was not an object")
        return payload

    async def create_customer(
        self, *, name: str, email: str | None, metadata: dict[str, str]
    ) -> dict[str, Any]:
        """Create a Stripe customer for a tenant."""
        form = {"name": name}
        if email:
            form["email"] = email
        form.update(_metadata_form(metadata))
        return await self._post("/customers", form)

    async def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        client_reference_id: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        """Create a Stripe-hosted subscription Checkout Session."""
        form = {
            "mode": "subscription",
            "customer": customer_id,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": client_reference_id,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
        }
        form.update(_metadata_form(metadata))
        return await self._post("/checkout/sessions", form)

    async def create_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> dict[str, Any]:
        """Create a Stripe-hosted Customer Portal Session."""
        return await self._post(
            "/billing_portal/sessions",
            {"customer": customer_id, "return_url": return_url},
        )


def _metadata_form(metadata: dict[str, str]) -> dict[str, str]:
    return {f"metadata[{key}]": value for key, value in metadata.items()}


def configured_checkout_urls(
    cfg: Config,
    *,
    success_url: str | None = None,
    cancel_url: str | None = None,
) -> BillingUrls:
    """Resolve Checkout redirect URLs from request overrides or configuration."""
    resolved_success = success_url or cfg.stripe_success_url
    resolved_cancel = cancel_url or cfg.stripe_cancel_url
    missing = [
        name
        for name, value in (
            ("success_url", resolved_success),
            ("cancel_url", resolved_cancel),
        )
        if not value
    ]
    if missing:
        raise BillingConfigurationError(
            "missing Stripe redirect configuration: " + ", ".join(missing)
        )
    return BillingUrls(
        success_url=str(resolved_success),
        cancel_url=str(resolved_cancel),
    )


def configured_portal_url(cfg: Config, *, return_url: str | None = None) -> PortalUrl:
    """Resolve the Customer Portal return URL from request override or config."""
    resolved = return_url or cfg.stripe_portal_return_url
    if not resolved:
        raise BillingConfigurationError("missing Stripe redirect configuration: portal_return_url")
    return PortalUrl(return_url=str(resolved))


def stripe_transport_from_config(cfg: Config) -> StripeRestTransport:
    """Build a Stripe transport from runtime configuration."""
    if not cfg.stripe_secret_key:
        raise BillingConfigurationError("DOCRAY_STRIPE_SECRET_KEY is required")
    return StripeRestTransport(secret_key=cfg.stripe_secret_key, api_base=cfg.stripe_api_base)


async def billing_status(conn: psycopg.AsyncConnection[Any], cfg: Config) -> dict[str, Any]:
    """Return current tenant billing state plus configuration readiness flags."""
    tenant_id = await billing_store.current_tenant_id(conn)
    status = await billing_store.get_billing_status(conn, tenant_id)
    status["checkout_ready"] = bool(
        cfg.stripe_secret_key
        and cfg.stripe_price_id
        and cfg.stripe_success_url
        and cfg.stripe_cancel_url
    )
    status["portal_ready"] = bool(cfg.stripe_secret_key and cfg.stripe_portal_return_url)
    entitlement = entitlement_from_status(status, cfg)
    status.update(entitlement)
    return status


def entitlement_from_status(status: dict[str, Any], cfg: Config) -> dict[str, Any]:
    """Return entitlement fields for a billing status row."""
    subscription_status = str(status.get("subscription_status") or "none")
    if not cfg.billing_enforcement_enabled:
        return {
            "enforcement_enabled": False,
            "entitled": True,
            "entitlement_reason": "billing enforcement is disabled",
        }
    if subscription_status in ENTITLED_SUBSCRIPTION_STATUSES:
        return {
            "enforcement_enabled": True,
            "entitled": True,
            "entitlement_reason": f"subscription is {subscription_status}",
        }
    return {
        "enforcement_enabled": True,
        "entitled": False,
        "entitlement_reason": f"subscription is {subscription_status}",
    }


async def require_entitlement(
    conn: psycopg.AsyncConnection[Any], cfg: Config | None
) -> None:
    """Require the current tenant to be entitled to paid SaaS actions."""
    if cfg is None or not cfg.billing_enforcement_enabled:
        return
    tenant_id = await billing_store.current_tenant_id(conn)
    status = await billing_store.get_billing_status(conn, tenant_id)
    entitlement = entitlement_from_status(status, cfg)
    if not entitlement["entitled"]:
        raise BillingEntitlementError(
            "billing subscription required: " + str(entitlement["entitlement_reason"])
        )


async def create_checkout_session(
    conn: psycopg.AsyncConnection[Any],
    cfg: Config,
    *,
    success_url: str | None = None,
    cancel_url: str | None = None,
    transport: StripeTransport | None = None,
) -> str:
    """Create a Stripe subscription Checkout Session for the current tenant."""
    if not cfg.stripe_price_id:
        raise BillingConfigurationError("DOCRAY_STRIPE_PRICE_ID is required")
    urls = configured_checkout_urls(cfg, success_url=success_url, cancel_url=cancel_url)
    stripe = transport or stripe_transport_from_config(cfg)
    tenant_id = await billing_store.current_tenant_id(conn)
    account = await billing_store.get_billing_account(conn, tenant_id)
    customer_id = account.get("stripe_customer_id") if account else None
    if not customer_id:
        customer = await stripe.create_customer(
            name=f"doc-ray tenant {tenant_id}",
            email=None,
            metadata={"tenant_id": tenant_id},
        )
        customer_id = str(customer["id"])
        await billing_store.upsert_billing_account(
            conn,
            tenant_id=tenant_id,
            stripe_customer_id=customer_id,
            billing_email=None,
            created_by_principal_id=None,
        )
    session = await stripe.create_checkout_session(
        customer_id=str(customer_id),
        price_id=cfg.stripe_price_id,
        success_url=urls.success_url,
        cancel_url=urls.cancel_url,
        client_reference_id=tenant_id,
        metadata={"tenant_id": tenant_id, "price_id": cfg.stripe_price_id},
    )
    url = session.get("url")
    if not isinstance(url, str) or not url:
        raise RuntimeError("Stripe Checkout Session did not include a url")
    return url


async def create_portal_session(
    conn: psycopg.AsyncConnection[Any],
    cfg: Config,
    *,
    return_url: str | None = None,
    transport: StripeTransport | None = None,
) -> str:
    """Create a Stripe Customer Portal Session for the current tenant."""
    url = configured_portal_url(cfg, return_url=return_url)
    stripe = transport or stripe_transport_from_config(cfg)
    tenant_id = await billing_store.current_tenant_id(conn)
    account = await billing_store.get_billing_account(conn, tenant_id)
    customer_id = account.get("stripe_customer_id") if account else None
    if not customer_id:
        raise BillingConfigurationError("tenant does not have a Stripe customer yet")
    session = await stripe.create_portal_session(
        customer_id=str(customer_id),
        return_url=url.return_url,
    )
    url = session.get("url")
    if not isinstance(url, str) or not url:
        raise RuntimeError("Stripe Portal Session did not include a url")
    return url


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
            timestamp = int(value)
        elif key == "v1":
            signatures.append(value)
    if timestamp is None or not signatures:
        raise StripeSignatureError("Stripe signature header is missing t/v1")
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        raise StripeSignatureError("Stripe signature timestamp is outside tolerance")
    signed_payload = f"{timestamp}.{payload.decode('utf-8')}".encode()
    expected = hmac.new(webhook_secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise StripeSignatureError("Stripe signature mismatch")
    event = json.loads(payload.decode("utf-8"))
    if not isinstance(event, dict):
        raise StripeSignatureError("Stripe event payload is not an object")
    return event


async def process_webhook_event(
    conn: psycopg.AsyncConnection[Any], event: dict[str, Any]
) -> bool:
    """Process one verified Stripe event; return false when already seen."""
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    if not event_id or not event_type:
        raise ValueError("Stripe event is missing id or type")
    inserted = await billing_store.insert_webhook_event(
        conn,
        stripe_event_id=event_id,
        event_type=event_type,
        livemode=bool(event.get("livemode", False)),
        payload=event,
    )
    if not inserted:
        return False
    obj = event.get("data", {}).get("object", {})
    if not isinstance(obj, dict):
        obj = {}
    if event_type == "checkout.session.completed":
        await _process_checkout_completed(conn, event_id, obj)
    elif event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        await _process_subscription_event(conn, event_id, obj)
    elif event_type in {"invoice.payment_succeeded", "invoice.payment_failed"}:
        await _process_invoice_event(conn, event_id, event_type, obj)
    await billing_store.mark_webhook_processed(conn, event_id)
    return True


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
) -> None:
    customer_id = _string(obj.get("customer"))
    if not customer_id:
        return
    await billing_store.update_invoice_snapshot(
        conn,
        stripe_customer_id=customer_id,
        latest_invoice_id=_string(obj.get("id")),
        fallback_status="past_due" if event_type == "invoice.payment_failed" else None,
        stripe_event_id=event_id,
    )


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
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
