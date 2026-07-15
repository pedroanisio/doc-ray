"""Stripe REST transport for hosted billing flows — split from :mod:`billing` (D-015).

The minimal Stripe API surface doc-ray needs (customers, checkout, portal), plus
its request-error translation. Depends only on :mod:`billing_base` primitives, so
it never imports the core flows or webhook processing.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from doc_ray.application.billing_base import StripeAPIError, _string


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

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> dict[str, Any]:
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
        return await self._post("/checkout/sessions", form, idempotency_key=idempotency_key)

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> dict[str, Any]:
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
