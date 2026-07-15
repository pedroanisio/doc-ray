"""Postgres adapter for tenant-scoped billing state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.types.json import Json

BillingRow = dict[str, Any]


async def current_tenant_id(conn: psycopg.AsyncConnection[Any]) -> str:
    """Return the tenant id bound to the current authenticated principal."""
    cur = await conn.execute("SELECT docray_current_tenant() AS tenant_id")
    row = await cur.fetchone()
    tenant_id = row.get("tenant_id") if row else None
    if tenant_id is None:
        raise PermissionError("billing requires an authenticated tenant principal")
    return str(tenant_id)


async def get_billing_status(
    conn: psycopg.AsyncConnection[Any], tenant_id: str
) -> BillingRow:
    """Return the billing snapshot for *tenant_id*."""
    cur = await conn.execute(
        """
        SELECT %s::uuid AS tenant_id,
               NULL::text AS tenant_slug,
               NULL::text AS tenant_name,
               a.stripe_customer_id,
               a.billing_email,
               s.stripe_subscription_id,
               s.stripe_price_id,
               s.status AS subscription_status,
               s.current_period_end,
               s.cancel_at_period_end,
               s.latest_invoice_id,
               s.checkout_session_id,
               s.updated_at AS subscription_updated_at
        FROM (SELECT %s::uuid AS tenant_id) t
        LEFT JOIN tenant_billing_accounts a ON a.tenant_id = t.tenant_id
        LEFT JOIN tenant_subscriptions s ON s.tenant_id = t.tenant_id
        """,
        (tenant_id, tenant_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise ValueError(f"unknown tenant {tenant_id}")
    return dict(row)


async def list_billing_events(
    conn: psycopg.AsyncConnection[Any], tenant_id: str, *, limit: int = 25
) -> list[BillingRow]:
    """Return recent Stripe webhook events visible for *tenant_id*."""
    bounded_limit = max(1, min(int(limit), 100))
    cur = await conn.execute(
        """
        WITH tenant_customer AS (
            SELECT stripe_customer_id
            FROM tenant_billing_accounts
            WHERE tenant_id = %s::uuid
        )
        SELECT e.stripe_event_id,
               e.event_type,
               e.livemode,
               e.processed_at,
               e.created_at,
               e.payload #>> '{data,object,id}' AS object_id,
               e.payload #>> '{data,object,customer}' AS customer_id,
               CASE
                   WHEN e.event_type LIKE 'customer.subscription.%%'
                       THEN e.payload #>> '{data,object,id}'
                   ELSE e.payload #>> '{data,object,subscription}'
               END AS subscription_id,
               e.payload #>> '{data,object,status}' AS object_status,
               e.payload #>> '{data,object,latest_invoice}' AS latest_invoice_id
        FROM stripe_webhook_events e
        LEFT JOIN tenant_customer tc ON true
        WHERE e.payload #>> '{data,object,client_reference_id}' = %s
           OR e.payload #>> '{data,object,metadata,tenant_id}' = %s
           OR (
                tc.stripe_customer_id IS NOT NULL
                AND e.payload #>> '{data,object,customer}' = tc.stripe_customer_id
           )
        ORDER BY e.created_at DESC, e.stripe_event_id DESC
        LIMIT %s
        """,
        (tenant_id, tenant_id, tenant_id, bounded_limit),
    )
    rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_billing_account(
    conn: psycopg.AsyncConnection[Any], tenant_id: str
) -> BillingRow | None:
    """Return one tenant billing account row, if present."""
    cur = await conn.execute(
        """
        SELECT tenant_id, stripe_customer_id, billing_email, created_by_principal_id
        FROM tenant_billing_accounts
        WHERE tenant_id = %s
        """,
        (tenant_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def upsert_billing_account(
    conn: psycopg.AsyncConnection[Any],
    *,
    tenant_id: str,
    stripe_customer_id: str,
    billing_email: str | None,
    created_by_principal_id: str | None,
) -> BillingRow:
    """Create or update the tenant billing account."""
    cur = await conn.execute(
        """
        INSERT INTO tenant_billing_accounts
            (tenant_id, stripe_customer_id, billing_email, created_by_principal_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            billing_email = COALESCE(EXCLUDED.billing_email, tenant_billing_accounts.billing_email)
        RETURNING tenant_id, stripe_customer_id, billing_email, created_by_principal_id
        """,
        (tenant_id, stripe_customer_id, billing_email, created_by_principal_id),
    )
    return dict(await cur.fetchone())


async def record_checkout_session(
    conn: psycopg.AsyncConnection[Any],
    *,
    tenant_id: str,
    stripe_customer_id: str,
    checkout_session_id: str,
    stripe_subscription_id: str | None,
    stripe_price_id: str | None,
    stripe_event_id: str | None = None,
) -> None:
    """Persist the checkout-created subscription pointer."""
    await conn.execute(
        """
        INSERT INTO tenant_subscriptions
            (tenant_id, stripe_customer_id, stripe_subscription_id, stripe_price_id,
             status, checkout_session_id, last_stripe_event_id)
        VALUES (%s, %s, %s, %s, 'checkout_completed', %s, %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_subscription_id = COALESCE(
                EXCLUDED.stripe_subscription_id,
                tenant_subscriptions.stripe_subscription_id
            ),
            stripe_price_id = COALESCE(
                EXCLUDED.stripe_price_id,
                tenant_subscriptions.stripe_price_id
            ),
            status = CASE
                WHEN tenant_subscriptions.status = 'none' THEN EXCLUDED.status
                ELSE tenant_subscriptions.status
            END,
            checkout_session_id = EXCLUDED.checkout_session_id,
            last_stripe_event_id = EXCLUDED.last_stripe_event_id
        """,
        (
            tenant_id,
            stripe_customer_id,
            stripe_subscription_id,
            stripe_price_id,
            checkout_session_id,
            stripe_event_id,
        ),
    )


async def upsert_subscription_snapshot(
    conn: psycopg.AsyncConnection[Any],
    *,
    tenant_id: str,
    stripe_customer_id: str,
    stripe_subscription_id: str,
    status: str,
    stripe_price_id: str | None,
    current_period_end: datetime | None,
    cancel_at_period_end: bool,
    latest_invoice_id: str | None,
    stripe_event_id: str | None,
) -> None:
    """Upsert the latest subscription state received from Stripe."""
    await conn.execute(
        """
        INSERT INTO tenant_subscriptions
            (tenant_id, stripe_customer_id, stripe_subscription_id, stripe_price_id, status,
             current_period_end, cancel_at_period_end, latest_invoice_id, last_stripe_event_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id) DO UPDATE
        SET stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_subscription_id = EXCLUDED.stripe_subscription_id,
            stripe_price_id = EXCLUDED.stripe_price_id,
            status = EXCLUDED.status,
            current_period_end = EXCLUDED.current_period_end,
            cancel_at_period_end = EXCLUDED.cancel_at_period_end,
            latest_invoice_id = EXCLUDED.latest_invoice_id,
            last_stripe_event_id = EXCLUDED.last_stripe_event_id
        """,
        (
            tenant_id,
            stripe_customer_id,
            stripe_subscription_id,
            stripe_price_id,
            status,
            current_period_end,
            cancel_at_period_end,
            latest_invoice_id,
            stripe_event_id,
        ),
    )


async def update_invoice_snapshot(
    conn: psycopg.AsyncConnection[Any],
    *,
    stripe_customer_id: str,
    stripe_subscription_id: str | None,
    latest_invoice_id: str | None,
    fallback_status: str | None,
    stripe_event_id: str | None,
) -> None:
    """Update invoice details for the matching tenant subscription."""
    status_clause = "status = COALESCE(%s, status),"
    await conn.execute(
        f"""
        UPDATE tenant_subscriptions
        SET {status_clause}
            latest_invoice_id = COALESCE(%s, latest_invoice_id),
            last_stripe_event_id = %s
        WHERE stripe_customer_id = %s
          AND (
              %s::text IS NULL
              OR stripe_subscription_id IS NULL
              OR stripe_subscription_id = %s
          )
        """,
        (
            fallback_status,
            latest_invoice_id,
            stripe_event_id,
            stripe_customer_id,
            stripe_subscription_id,
            stripe_subscription_id,
        ),
    )


async def tenant_for_stripe_customer(
    conn: psycopg.AsyncConnection[Any], stripe_customer_id: str
) -> str | None:
    """Return the tenant mapped to a Stripe customer id."""
    cur = await conn.execute(
        """
        SELECT tenant_id
        FROM tenant_billing_accounts
        WHERE stripe_customer_id = %s
        """,
        (stripe_customer_id,),
    )
    row = await cur.fetchone()
    return str(row["tenant_id"]) if row else None


async def insert_webhook_event(
    conn: psycopg.AsyncConnection[Any],
    *,
    stripe_event_id: str,
    event_type: str,
    livemode: bool,
    payload: dict[str, Any],
) -> bool:
    """Insert or claim a webhook event ledger row.

    Returns false only when the event was already processed. Existing unprocessed
    rows are returned for retry so Stripe delivery retries cannot get stuck
    behind an earlier failed processing attempt.
    """
    cur = await conn.execute(
        """
        WITH inserted AS (
            INSERT INTO stripe_webhook_events
                (stripe_event_id, event_type, livemode, payload)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (stripe_event_id) DO NOTHING
            RETURNING TRUE AS should_process
        ),
        existing AS (
            SELECT processed_at IS NULL AS should_process
            FROM stripe_webhook_events
            WHERE stripe_event_id = %s
              AND NOT EXISTS (SELECT 1 FROM inserted)
        )
        SELECT should_process FROM inserted
        UNION ALL
        SELECT should_process FROM existing
        LIMIT 1
        """,
        (stripe_event_id, event_type, livemode, Json(payload), stripe_event_id),
    )
    row = await cur.fetchone()
    return bool(row and row["should_process"])


async def mark_webhook_processed(conn: psycopg.AsyncConnection[Any], stripe_event_id: str) -> None:
    """Mark a webhook event as processed."""
    await conn.execute(
        """
        UPDATE stripe_webhook_events
        SET processed_at = %s
        WHERE stripe_event_id = %s
        """,
        (datetime.now(timezone.utc), stripe_event_id),
    )
