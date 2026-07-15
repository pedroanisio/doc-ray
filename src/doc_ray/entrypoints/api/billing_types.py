"""GraphQL types for tenant billing."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import strawberry


@strawberry.type
class BillingStatus:
    """Tenant billing state visible to tenant admins."""

    tenant_id: strawberry.ID
    tenant_slug: str
    tenant_name: str
    stripe_customer_id: str | None
    billing_email: str | None
    stripe_subscription_id: str | None
    stripe_price_id: str | None
    subscription_status: str
    current_period_end: datetime | None
    cancel_at_period_end: bool
    latest_invoice_id: str | None
    checkout_session_id: str | None
    subscription_updated_at: datetime | None
    checkout_ready: bool
    portal_ready: bool
    webhook_ready: bool
    webhook_endpoint_url: str | None
    live_mode_ready: bool
    missing_configuration: list[str]
    required_webhook_events: list[str]
    enforcement_enabled: bool
    entitled: bool
    entitlement_reason: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> BillingStatus:
        """Build a GraphQL billing status object from an adapter row."""
        return cls(
            tenant_id=strawberry.ID(str(row["tenant_id"])),
            tenant_slug=row.get("tenant_slug") or "current",
            tenant_name=row.get("tenant_name") or "Current tenant",
            stripe_customer_id=row.get("stripe_customer_id"),
            billing_email=row.get("billing_email"),
            stripe_subscription_id=row.get("stripe_subscription_id"),
            stripe_price_id=row.get("stripe_price_id"),
            subscription_status=row.get("subscription_status") or "none",
            current_period_end=row.get("current_period_end"),
            cancel_at_period_end=bool(row.get("cancel_at_period_end", False)),
            latest_invoice_id=row.get("latest_invoice_id"),
            checkout_session_id=row.get("checkout_session_id"),
            subscription_updated_at=row.get("subscription_updated_at"),
            checkout_ready=bool(row.get("checkout_ready", False)),
            portal_ready=bool(row.get("portal_ready", False)),
            webhook_ready=bool(row.get("webhook_ready", False)),
            webhook_endpoint_url=row.get("webhook_endpoint_url"),
            live_mode_ready=bool(row.get("live_mode_ready", False)),
            missing_configuration=list(row.get("missing_configuration") or []),
            required_webhook_events=list(row.get("required_webhook_events") or []),
            enforcement_enabled=bool(row.get("enforcement_enabled", False)),
            entitled=bool(row.get("entitled", True)),
            entitlement_reason=row.get("entitlement_reason") or "",
        )


@strawberry.type
class BillingEvent:
    """Tenant-visible Stripe webhook activity."""

    stripe_event_id: strawberry.ID
    event_type: str
    livemode: bool
    processed_at: datetime | None
    created_at: datetime
    object_id: str | None
    customer_id: str | None
    subscription_id: str | None
    object_status: str | None
    latest_invoice_id: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> BillingEvent:
        """Build a GraphQL billing event object from an adapter row."""
        return cls(
            stripe_event_id=strawberry.ID(str(row["stripe_event_id"])),
            event_type=str(row["event_type"]),
            livemode=bool(row.get("livemode", False)),
            processed_at=row.get("processed_at"),
            created_at=row["created_at"],
            object_id=row.get("object_id"),
            customer_id=row.get("customer_id"),
            subscription_id=row.get("subscription_id"),
            object_status=row.get("object_status"),
            latest_invoice_id=row.get("latest_invoice_id"),
        )


@strawberry.type
class BillingSession:
    """Hosted Stripe session URL returned to the admin console."""

    url: str
