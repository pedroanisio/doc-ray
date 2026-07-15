"""Stripe billing orchestration for tenant subscriptions."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import ParseResult, urlparse

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


class StripeAPIError(RuntimeError):
    """Raised when Stripe rejects or cannot complete an API request."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        stripe_type: str | None = None,
        stripe_code: str | None = None,
        decline_code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Initialize a sanitized Stripe API error."""
        super().__init__(message)
        self.status_code = status_code
        self.stripe_type = stripe_type
        self.stripe_code = stripe_code
        self.decline_code = decline_code
        self.request_id = request_id


class WebhookProcessingPending(RuntimeError):
    """Raised when a Stripe webhook should be retried later."""


ENTITLED_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing"})
CHECKOUT_BLOCKING_SUBSCRIPTION_STATUSES = ENTITLED_SUBSCRIPTION_STATUSES | frozenset(
    {"checkout_pending", "checkout_completed", "incomplete", "past_due", "unpaid"}
)
REQUIRED_WEBHOOK_EVENTS = (
    "checkout.session.completed",
    "checkout.session.expired",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
    "invoice.finalization_failed",
)


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
        self,
        *,
        name: str,
        email: str | None,
        metadata: dict[str, str],
        idempotency_key: str,
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
        subscription_metadata: dict[str, str],
        idempotency_key: str,
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

    async def _post(
        self, path: str, form: dict[str, str], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        headers = {"authorization": f"Bearer {self._secret_key}"}
        if idempotency_key:
            headers["idempotency-key"] = idempotency_key
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self._api_base}{path}",
                    data=form,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            message = f"Stripe request failed before receiving a response: {exc}"
            raise StripeAPIError(message) from exc
        if response.status_code >= 400:
            raise _stripe_api_error(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Stripe response was not an object")
        return payload

    async def create_customer(
        self,
        *,
        name: str,
        email: str | None,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create a Stripe customer for a tenant."""
        form = {"name": name}
        if email:
            form["email"] = email
        form.update(_metadata_form(metadata))
        return await self._post("/customers", form, idempotency_key=idempotency_key)

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
        idempotency_key: str,
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
        form.update(_metadata_form(subscription_metadata, prefix="subscription_data[metadata]"))
        return await self._post(
            "/checkout/sessions", form, idempotency_key=idempotency_key
        )

    async def create_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> dict[str, Any]:
        """Create a Stripe-hosted Customer Portal Session."""
        return await self._post(
            "/billing_portal/sessions",
            {"customer": customer_id, "return_url": return_url},
        )


def _metadata_form(metadata: dict[str, str], *, prefix: str = "metadata") -> dict[str, str]:
    return {f"{prefix}[{key}]": value for key, value in metadata.items()}


def _stripe_api_error(response: httpx.Response) -> StripeAPIError:
    error_obj: dict[str, Any] = {}
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        error_obj = payload["error"]

    stripe_type = _string(error_obj.get("type"))
    stripe_code = _string(error_obj.get("code"))
    decline_code = _string(error_obj.get("decline_code"))
    request_id = _string(response.headers.get("request-id")) or _string(
        response.headers.get("stripe-request-id")
    )
    detail = _string(error_obj.get("message"))
    parts = [f"Stripe request failed with HTTP {response.status_code}"]
    for label, value in (
        ("type", stripe_type),
        ("code", stripe_code),
        ("decline_code", decline_code),
        ("request_id", request_id),
    ):
        if value:
            parts.append(f"{label}={value}")
    message = ", ".join(parts)
    if detail:
        message = f"{message}: {detail}"
    return StripeAPIError(
        message,
        status_code=response.status_code,
        stripe_type=stripe_type,
        stripe_code=stripe_code,
        decline_code=decline_code,
        request_id=request_id,
    )


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
    urls = BillingUrls(
        success_url=str(resolved_success),
        cancel_url=str(resolved_cancel),
    )
    _validate_hosted_redirect_url(
        cfg,
        urls.success_url,
        field="success_url",
        configured_url=cfg.stripe_success_url,
    )
    _validate_hosted_redirect_url(
        cfg,
        urls.cancel_url,
        field="cancel_url",
        configured_url=cfg.stripe_cancel_url,
    )
    return urls


def configured_portal_url(cfg: Config, *, return_url: str | None = None) -> PortalUrl:
    """Resolve the Customer Portal return URL from request override or config."""
    resolved = return_url or cfg.stripe_portal_return_url
    if not resolved:
        raise BillingConfigurationError("missing Stripe redirect configuration: portal_return_url")
    url = PortalUrl(return_url=str(resolved))
    _validate_hosted_redirect_url(
        cfg,
        url.return_url,
        field="portal_return_url",
        configured_url=cfg.stripe_portal_return_url,
    )
    return url


def _validate_hosted_redirect_url(
    cfg: Config,
    value: str,
    *,
    field: str,
    configured_url: str | None,
) -> None:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BillingConfigurationError(
            f"invalid Stripe redirect configuration: {field} must be an absolute URL"
        )
    if cfg.billing_enforcement_enabled and parsed.scheme != "https":
        raise BillingConfigurationError(
            f"invalid Stripe redirect configuration: {field} must use https for live billing"
        )

    expected_origin = _configured_redirect_origin(cfg, configured_url)
    if expected_origin is not None and _url_origin(parsed) != expected_origin:
        raise BillingConfigurationError(
            f"invalid Stripe redirect configuration: {field} must use the configured "
            "application origin"
        )


def _configured_redirect_origin(cfg: Config, configured_url: str | None) -> str | None:
    for value in (configured_url, cfg.public_base_url):
        parsed = urlparse((value or "").strip())
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return _url_origin(parsed)
    return None


def _url_origin(parsed: ParseResult) -> str:
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


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
    status["webhook_endpoint_url"] = webhook_endpoint_url(cfg)
    status["webhook_ready"] = bool(cfg.stripe_webhook_secret and status["webhook_endpoint_url"])
    status["required_webhook_events"] = list(REQUIRED_WEBHOOK_EVENTS)
    status["missing_configuration"] = missing_billing_configuration(cfg)
    status["configuration_issues"] = invalid_billing_configuration(cfg)
    status["live_mode_ready"] = not status["missing_configuration"] and not status[
        "configuration_issues"
    ]
    entitlement = entitlement_from_status(status, cfg)
    status.update(entitlement)
    return status


async def billing_events(
    conn: psycopg.AsyncConnection[Any], *, limit: int = 25
) -> list[dict[str, Any]]:
    """Return recent tenant-visible Stripe webhook events."""
    tenant_id = await billing_store.current_tenant_id(conn)
    return await billing_store.list_billing_events(conn, tenant_id, limit=limit)


def webhook_endpoint_url(cfg: Config) -> str | None:
    """Return the public Stripe webhook endpoint URL when configured."""
    base = (cfg.public_base_url or "").strip().rstrip("/")
    if not base:
        return None
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{base}/stripe/webhook"


def missing_billing_configuration(cfg: Config) -> list[str]:
    """Return non-secret configuration names still required for live billing."""
    missing: list[str] = []
    required_values = (
        ("DOCRAY_STRIPE_SECRET_KEY", cfg.stripe_secret_key),
        ("DOCRAY_STRIPE_WEBHOOK_SECRET", cfg.stripe_webhook_secret),
        ("DOCRAY_STRIPE_PRICE_ID", cfg.stripe_price_id),
        ("DOCRAY_STRIPE_SUCCESS_URL", cfg.stripe_success_url),
        ("DOCRAY_STRIPE_CANCEL_URL", cfg.stripe_cancel_url),
        ("DOCRAY_STRIPE_PORTAL_RETURN_URL", cfg.stripe_portal_return_url),
        ("DOCRAY_PUBLIC_BASE_URL", cfg.public_base_url),
    )
    for name, value in required_values:
        if not value:
            missing.append(name)
    if not cfg.billing_enforcement_enabled:
        missing.append("DOCRAY_BILLING_ENFORCEMENT_ENABLED=true")
    return missing


def invalid_billing_configuration(cfg: Config) -> list[str]:
    """Return configured billing values that are unsafe for live payments."""
    issues: list[str] = []
    if not cfg.billing_enforcement_enabled:
        return issues

    if _looks_like_stripe_test_secret(cfg.stripe_secret_key):
        issues.append("DOCRAY_STRIPE_SECRET_KEY uses a Stripe test-mode key")

    for name, value in (
        ("DOCRAY_STRIPE_SUCCESS_URL", cfg.stripe_success_url),
        ("DOCRAY_STRIPE_CANCEL_URL", cfg.stripe_cancel_url),
        ("DOCRAY_STRIPE_PORTAL_RETURN_URL", cfg.stripe_portal_return_url),
        ("DOCRAY_PUBLIC_BASE_URL", cfg.public_base_url),
    ):
        if value and not _is_https_url(value):
            issues.append(f"{name} must use https for live billing")

    return issues


def _require_checkout_live_readiness(cfg: Config) -> None:
    """Refuse hosted Checkout when configured for live collection but not ready."""
    if not cfg.billing_enforcement_enabled and _expected_stripe_livemode(cfg) is not True:
        return
    missing = missing_billing_configuration(cfg)
    issues = invalid_billing_configuration(cfg)
    if not missing and not issues:
        return
    message = "Stripe Checkout is not live-ready"
    if missing:
        message += ": missing " + ", ".join(missing)
    if issues:
        separator = "; " if missing else ": "
        message += separator + "fix " + ", ".join(issues)
    raise BillingConfigurationError(message)


def _looks_like_stripe_test_secret(value: str | None) -> bool:
    return bool(value and value.startswith(("sk_test_", "rk_test_")))


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


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
        configured_price_id = cfg.stripe_price_id
        subscription_price_id = status.get("stripe_price_id")
        if configured_price_id and subscription_price_id != configured_price_id:
            return {
                "enforcement_enabled": True,
                "entitled": False,
                "entitlement_reason": "subscription price does not match configured Stripe price",
            }
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
    _require_checkout_live_readiness(cfg)
    urls = configured_checkout_urls(cfg, success_url=success_url, cancel_url=cancel_url)
    stripe = transport or stripe_transport_from_config(cfg)
    tenant_id = await billing_store.current_tenant_id(conn)
    status = await billing_store.get_billing_status(conn, tenant_id)
    subscription_status = str(status.get("subscription_status") or "none")
    if subscription_status in CHECKOUT_BLOCKING_SUBSCRIPTION_STATUSES:
        raise BillingConfigurationError(
            "tenant already has an "
            f"{subscription_status} Stripe subscription state; use the billing portal"
        )
    account = await billing_store.get_billing_account(conn, tenant_id)
    customer_id = account.get("stripe_customer_id") if account else None
    if not customer_id:
        customer = await stripe.create_customer(
            name=f"doc-ray tenant {tenant_id}",
            email=None,
            metadata={"tenant_id": tenant_id},
            idempotency_key=f"docray.customer.{tenant_id}",
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
        subscription_metadata={"tenant_id": tenant_id, "price_id": cfg.stripe_price_id},
        idempotency_key=f"docray.checkout.{tenant_id}.{cfg.stripe_price_id}",
    )
    session_id = session.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("Stripe Checkout Session did not include an id")
    url = session.get("url")
    if not isinstance(url, str) or not url:
        raise RuntimeError("Stripe Checkout Session did not include a url")
    await billing_store.record_checkout_session_started(
        conn,
        tenant_id=tenant_id,
        stripe_customer_id=str(customer_id),
        checkout_session_id=session_id,
        stripe_price_id=cfg.stripe_price_id,
    )
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
    if not handled:
        raise WebhookProcessingPending(
            "Stripe webhook is pending tenant mapping; retry delivery later"
        )
    await billing_store.mark_webhook_processed(conn, event_id)
    return True


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
    updated = await billing_store.update_invoice_snapshot(
        conn,
        stripe_customer_id=customer_id,
        stripe_subscription_id=_string(obj.get("subscription")),
        latest_invoice_id=_string(obj.get("id")),
        fallback_status=_invoice_fallback_status(event_type),
        stripe_event_id=event_id,
    )
    return updated or event_type not in _PAID_INVOICE_EVENTS


def _invoice_fallback_status(event_type: str) -> str | None:
    if event_type in _FAILED_INVOICE_EVENTS:
        return "past_due"
    if event_type in _PAID_INVOICE_EVENTS:
        return "active"
    return None


_FAILED_INVOICE_EVENTS = frozenset({"invoice.payment_failed", "invoice.finalization_failed"})
_PAID_INVOICE_EVENTS = frozenset({"invoice.paid", "invoice.payment_succeeded"})


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
