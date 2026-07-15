"""Stripe billing orchestration for tenant subscriptions.

The core billing flows (config/redirect validation, entitlement, checkout,
portal, status/events, webhook retry). The transport lives in
:mod:`billing_stripe`, webhook processing in :mod:`billing_webhooks`, and shared
primitives in :mod:`billing_base` (D-015 split). This module re-exports the
public names those modules define so ``from doc_ray.application import billing``
keeps exposing the whole surface (``billing.StripeAPIError``,
``billing.verify_stripe_signature``, ...) as before.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import ParseResult, urlparse

import psycopg

from doc_ray.adapters import billing_postgres as billing_store

# Names used only inside this module are imported plainly; names re-exported for
# callers (``billing.X``) use the ``X as X`` form so mypy's no-implicit-reexport
# treats them as an explicit public surface after the D-015 split.
from doc_ray.application.billing_base import (
    CHECKOUT_BLOCKING_SUBSCRIPTION_STATUSES,
    ENTITLED_SUBSCRIPTION_STATUSES,
    BillingUrls,
    PortalUrl,
    _string,
)
from doc_ray.application.billing_base import (
    REQUIRED_WEBHOOK_EVENTS as REQUIRED_WEBHOOK_EVENTS,
)
from doc_ray.application.billing_base import BillingConfigurationError as BillingConfigurationError
from doc_ray.application.billing_base import BillingEntitlementError as BillingEntitlementError
from doc_ray.application.billing_base import StripeAPIError as StripeAPIError
from doc_ray.application.billing_base import StripeSignatureError as StripeSignatureError
from doc_ray.application.billing_base import WebhookProcessingPending as WebhookProcessingPending
from doc_ray.application.billing_stripe import StripeRestTransport as StripeRestTransport
from doc_ray.application.billing_stripe import StripeTransport
from doc_ray.application.billing_webhooks import (
    _expected_stripe_livemode,
    _handle_webhook_event,
    validate_webhook_event_mode,
)
from doc_ray.application.billing_webhooks import process_webhook_event as process_webhook_event
from doc_ray.application.billing_webhooks import verify_stripe_signature as verify_stripe_signature
from doc_ray.config import Config


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


def _checkout_idempotency_key(
    *, tenant_id: str, price_id: str, status: dict[str, Any], urls: BillingUrls
) -> str:
    attempt_state = {
        "tenant_id": tenant_id,
        "price_id": price_id,
        "checkout_session_id": status.get("checkout_session_id"),
        "subscription_updated_at": _idempotency_value(status.get("subscription_updated_at")),
        "success_url": urls.success_url,
        "cancel_url": urls.cancel_url,
    }
    raw = json.dumps(attempt_state, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"docray.checkout.{tenant_id}.{digest}"


def _idempotency_value(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return _string(value)


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
    status["live_mode_ready"] = (
        not status["missing_configuration"] and not status["configuration_issues"]
    )
    entitlement = entitlement_from_status(status, cfg)
    status.update(entitlement)
    return status


async def billing_events(
    conn: psycopg.AsyncConnection[Any], *, limit: int = 25
) -> list[dict[str, Any]]:
    """Return recent tenant-visible Stripe webhook events."""
    tenant_id = await billing_store.current_tenant_id(conn)
    return await billing_store.list_billing_events(conn, tenant_id, limit=limit)


async def retry_billing_event(
    conn: psycopg.AsyncConnection[Any], cfg: Config, stripe_event_id: str
) -> dict[str, Any]:
    """Retry one unprocessed tenant-visible Stripe webhook event."""
    tenant_id = await billing_store.current_tenant_id(conn)
    row = await billing_store.get_billing_event(conn, tenant_id, stripe_event_id)
    if row is None:
        raise ValueError(f"unknown billing event {stripe_event_id}")
    if row.get("processed_at") is not None:
        return row
    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"billing event {stripe_event_id} does not have an object payload")
    event_id = str(payload.get("id") or "")
    event_type = str(payload.get("type") or "")
    if event_id != stripe_event_id or not event_type:
        raise ValueError(f"billing event {stripe_event_id} payload does not match its ledger row")
    validate_webhook_event_mode(payload, cfg)
    handled = await _handle_webhook_event(conn, event_id, event_type, payload)
    if not handled:
        raise WebhookProcessingPending(
            "Stripe webhook is pending tenant mapping; retry delivery later"
        )
    await billing_store.mark_webhook_processed(conn, event_id)
    updated = await billing_store.get_billing_event(conn, tenant_id, stripe_event_id)
    if updated is None:  # pragma: no cover - row was just loaded and retried
        raise RuntimeError(f"billing event {stripe_event_id} disappeared after retry")
    return updated


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


def _require_hosted_billing_live_readiness(cfg: Config, flow_name: str) -> None:
    """Refuse hosted Stripe billing flows when live collection is not ready."""
    if not cfg.billing_enforcement_enabled and _expected_stripe_livemode(cfg) is not True:
        return
    missing = missing_billing_configuration(cfg)
    issues = invalid_billing_configuration(cfg)
    if not missing and not issues:
        return
    message = f"{flow_name} is not live-ready"
    if missing:
        message += ": missing " + ", ".join(missing)
    if issues:
        separator = "; " if missing else ": "
        message += separator + "fix " + ", ".join(issues)
    raise BillingConfigurationError(message)


def _require_hosted_payment_processing_readiness(cfg: Config) -> None:
    """Refuse hosted billing when Stripe events cannot be processed afterward."""
    missing: list[str] = []
    if not cfg.stripe_webhook_secret:
        missing.append("DOCRAY_STRIPE_WEBHOOK_SECRET")
    if webhook_endpoint_url(cfg) is None:
        missing.append("DOCRAY_PUBLIC_BASE_URL")
    if missing:
        raise BillingConfigurationError(
            "Stripe webhook processing is not configured: missing " + ", ".join(missing)
        )


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


async def require_entitlement(conn: psycopg.AsyncConnection[Any], cfg: Config | None) -> None:
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
    billing_email: str | None = None,
    created_by_principal_id: str | None = None,
    transport: StripeTransport | None = None,
) -> str:
    """Create a Stripe subscription Checkout Session for the current tenant."""
    if not cfg.stripe_price_id:
        raise BillingConfigurationError("DOCRAY_STRIPE_PRICE_ID is required")
    _require_hosted_billing_live_readiness(cfg, "Stripe Checkout")
    _require_hosted_payment_processing_readiness(cfg)
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
    normalized_billing_email = _normalize_billing_email(billing_email)
    if not customer_id:
        customer = await stripe.create_customer(
            name=f"doc-ray tenant {tenant_id}",
            email=normalized_billing_email,
            metadata={"tenant_id": tenant_id},
            idempotency_key=f"docray.customer.{tenant_id}",
        )
        customer_id = str(customer["id"])
        await billing_store.upsert_billing_account(
            conn,
            tenant_id=tenant_id,
            stripe_customer_id=customer_id,
            billing_email=normalized_billing_email,
            created_by_principal_id=created_by_principal_id,
        )
    session = await stripe.create_checkout_session(
        customer_id=str(customer_id),
        price_id=cfg.stripe_price_id,
        success_url=urls.success_url,
        cancel_url=urls.cancel_url,
        client_reference_id=tenant_id,
        metadata={"tenant_id": tenant_id, "price_id": cfg.stripe_price_id},
        subscription_metadata={"tenant_id": tenant_id, "price_id": cfg.stripe_price_id},
        idempotency_key=_checkout_idempotency_key(
            tenant_id=tenant_id,
            price_id=cfg.stripe_price_id,
            status=status,
            urls=urls,
        ),
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


def billing_email_from_principal_subject(subject: str | None) -> str | None:
    """Return a verified local-auth email from a principal subject, if present."""
    if subject is None or not subject.startswith("local:"):
        return None
    return _normalize_billing_email(subject.removeprefix("local:"))


def _normalize_billing_email(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        return None
    return normalized


async def create_portal_session(
    conn: psycopg.AsyncConnection[Any],
    cfg: Config,
    *,
    return_url: str | None = None,
    transport: StripeTransport | None = None,
) -> str:
    """Create a Stripe Customer Portal Session for the current tenant."""
    url = configured_portal_url(cfg, return_url=return_url)
    _require_hosted_billing_live_readiness(cfg, "Stripe Customer Portal")
    _require_hosted_payment_processing_readiness(cfg)
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
    portal_url = session.get("url")
    if not isinstance(portal_url, str) or not portal_url:
        raise RuntimeError("Stripe Portal Session did not include a url")
    return portal_url
