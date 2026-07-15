"""FastAPI routes for Stripe billing webhooks."""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Request
from psycopg_pool import AsyncConnectionPool

from doc_ray.application import billing
from doc_ray.config import Config


async def _stripe_webhook_endpoint(request: Request) -> dict[str, Any]:
    """Accept signed Stripe webhook events and update billing snapshots."""
    settings: Config = request.app.state.settings
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Stripe webhook secret is not configured")
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    if not signature:
        raise HTTPException(status_code=400, detail="missing Stripe-Signature header")
    try:
        event = billing.verify_stripe_signature(
            payload,
            signature,
            settings.stripe_webhook_secret,
        )
    except billing.StripeSignatureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pool: AsyncConnectionPool = request.app.state.pool
    async with pool.connection() as raw_conn:
        conn = raw_conn
        if isinstance(conn, psycopg.AsyncConnection):
            try:
                processed = await billing.process_webhook_event(conn, event, settings)
            except billing.BillingConfigurationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:  # pragma: no cover - pool always yields psycopg connections
            processed = False
    return {"received": True, "processed": processed}


def register_billing_routes(app: FastAPI) -> None:
    """Register Stripe billing routes."""
    app.add_api_route("/stripe/webhook", _stripe_webhook_endpoint, methods=["POST"])
