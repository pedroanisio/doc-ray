"""Shared billing primitives — errors, constants, dataclasses, leaf helpers.

Split from :mod:`billing` (D-015) as the dependency leaf: the transport
(:mod:`billing_stripe`), webhook processing (:mod:`billing_webhooks`), and the
core flows (:mod:`billing`) all import from here, and this module imports none of
them, so there is no import cycle. Re-exported through ``billing`` for callers.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    "invoice.payment_succeeded",
    "invoice.payment_failed",
    "invoice.finalization_failed",
)
FAILED_INVOICE_EVENTS = frozenset({"invoice.payment_failed", "invoice.finalization_failed"})
PAID_INVOICE_EVENTS = frozenset({"invoice.paid", "invoice.payment_succeeded"})
ACTIONABLE_INVOICE_EVENTS = FAILED_INVOICE_EVENTS | PAID_INVOICE_EVENTS


@dataclass(frozen=True)
class BillingUrls:
    """Redirect URLs used by hosted Stripe flows."""

    success_url: str
    cancel_url: str


@dataclass(frozen=True)
class PortalUrl:
    """Redirect URL used by Stripe Customer Portal."""

    return_url: str


def _string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
