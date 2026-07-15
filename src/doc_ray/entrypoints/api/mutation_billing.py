"""Billing GraphQL mutations, split from :mod:`mutation` (D-015).

A ``BillingMutations`` mixin the ``Mutation`` root inherits from — same pattern
as ``CollectionMutations``. Each resolver requires an admin principal, calls the
``application.billing`` use case, audits the outcome, and returns a typed result.
"""

from __future__ import annotations

import strawberry

from doc_ray.application import billing
from doc_ray.entrypoints.api.billing_types import BillingEvent, BillingSession
from doc_ray.entrypoints.api.mutation_guards import _audit_mutation_event, _require_admin
from doc_ray.entrypoints.api.types import Info


@strawberry.type
class BillingMutations:
    """Stripe billing write operations (checkout, portal, webhook retry)."""

    @strawberry.mutation
    async def create_billing_checkout_session(
        self,
        info: Info,
        success_url: str | None = None,
        cancel_url: str | None = None,
    ) -> BillingSession:
        """Create a Stripe hosted subscription Checkout Session for this tenant."""
        ctx = info.context
        await _require_admin(ctx, action="mutation.create_billing_checkout_session")
        settings = ctx.settings
        if settings is None and ctx.request is not None:
            settings = ctx.request.app.state.settings
        if settings is None:
            raise RuntimeError("billing settings are unavailable")
        url = await billing.create_checkout_session(
            ctx.conn,
            settings,
            success_url=success_url,
            cancel_url=cancel_url,
            billing_email=billing.billing_email_from_principal_subject(ctx.principal.subject),
            created_by_principal_id=ctx.principal.principal_id,
        )
        await _audit_mutation_event(
            ctx,
            action="mutation.create_billing_checkout_session",
            target_type="tenant_billing",
            target_id=None,
            outcome="success",
        )
        return BillingSession(url=url)

    @strawberry.mutation
    async def create_billing_portal_session(
        self,
        info: Info,
        return_url: str | None = None,
    ) -> BillingSession:
        """Create a Stripe hosted Customer Portal Session for this tenant."""
        ctx = info.context
        await _require_admin(ctx, action="mutation.create_billing_portal_session")
        settings = ctx.settings
        if settings is None and ctx.request is not None:
            settings = ctx.request.app.state.settings
        if settings is None:
            raise RuntimeError("billing settings are unavailable")
        url = await billing.create_portal_session(ctx.conn, settings, return_url=return_url)
        await _audit_mutation_event(
            ctx,
            action="mutation.create_billing_portal_session",
            target_type="tenant_billing",
            target_id=None,
            outcome="success",
        )
        return BillingSession(url=url)

    @strawberry.mutation
    async def retry_billing_event(
        self,
        info: Info,
        stripe_event_id: strawberry.ID,
    ) -> BillingEvent:
        """Retry a tenant-visible Stripe webhook event that has not processed yet."""
        ctx = info.context
        await _require_admin(ctx, action="mutation.retry_billing_event")
        settings = ctx.settings
        if settings is None and ctx.request is not None:
            settings = ctx.request.app.state.settings
        if settings is None:
            raise RuntimeError("billing settings are unavailable")
        row = await billing.retry_billing_event(ctx.conn, settings, str(stripe_event_id))
        await _audit_mutation_event(
            ctx,
            action="mutation.retry_billing_event",
            target_type="stripe_webhook_event",
            target_id=str(stripe_event_id),
            outcome="success",
        )
        return BillingEvent.from_row(row)
