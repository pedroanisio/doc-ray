"""Shared fixtures/helpers for the split billing test suite."""

from __future__ import annotations

import hmac
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

import _itdb
from _mutations_common import WRITE_PRINCIPAL

from doc_ray.config import Config

_MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"


class FakeStripe:
    """In-memory Stripe transport for deterministic tests."""

    def __init__(self) -> None:
        self.created_customers: list[dict[str, Any]] = []
        self.checkout_calls: list[dict[str, Any]] = []
        self.portal_calls: list[dict[str, Any]] = []

    async def create_customer(
        self,
        *,
        name: str,
        email: str | None,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.created_customers.append(
            {
                "name": name,
                "email": email,
                "metadata": metadata,
                "idempotency_key": idempotency_key,
            }
        )
        return {"id": "cus_test_123"}

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
        self.checkout_calls.append(
            {
                "customer_id": customer_id,
                "price_id": price_id,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "client_reference_id": client_reference_id,
                "metadata": metadata,
                "subscription_metadata": subscription_metadata,
                "idempotency_key": idempotency_key,
            }
        )
        return {"id": "cs_test_123", "url": "https://checkout.stripe.test/session"}

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> dict[str, Any]:
        self.portal_calls.append({"customer_id": customer_id, "return_url": return_url})
        return {"id": "bps_test_123", "url": "https://billing.stripe.test/session"}


def _cfg(*, enforcement_enabled: bool = False) -> Config:
    base = Config.from_env()
    return replace(
        base,
        database_url=_itdb.DB_URL,
        stripe_secret_key="sk_test_x",
        stripe_webhook_secret="whsec_test",
        stripe_price_id="price_test_monthly",
        stripe_success_url="https://app.example.com/billing/success",
        stripe_cancel_url="https://app.example.com/billing/cancel",
        stripe_portal_return_url="https://app.example.com/billing",
        public_base_url="https://app.example.com",
        billing_enforcement_enabled=enforcement_enabled,
        # These tests use createBackup as their sample paid mutation, so the
        # backup subsystem must be on or the feature gate answers before the
        # entitlement check ever runs (D-033).
        backup_enabled=True,
    )


def _apply_billing_migration(conn) -> str:
    conn.execute((_MIGRATIONS / "080_billing.sql").read_text())
    conn.execute((_MIGRATIONS / "081_schema_version_1_25_0.sql").read_text())
    conn.execute((_MIGRATIONS / "083_billing_event_read.sql").read_text())
    conn.execute((_MIGRATIONS / "084_schema_version_1_26_0.sql").read_text())
    conn.execute((_MIGRATIONS / "085_tenant_catalog_client_select.sql").read_text())
    conn.execute((_MIGRATIONS / "086_schema_version_1_27_0.sql").read_text())
    conn.execute((_MIGRATIONS / "088_billing_event_retry.sql").read_text())
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "billing-test-admin")
    return str(conn.execute("SELECT docray_default_tenant_id() AS id").fetchone()["id"])


def _signed_header(payload: bytes, secret: str, timestamp: int) -> str:
    digest = hmac.new(
        secret.encode(), f"{timestamp}.{payload.decode()}".encode(), sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def _prod_example_webhook_events() -> list[str]:
    text = (_MIGRATIONS.parents[1] / ".env.prod.example").read_text()
    # Anchor on the variable, then look BACK for the marker. Anchoring on the
    # marker first breaks whenever another entry mentions the same phrase — the
    # samples are generated from config/env.registry.yaml and sorted
    # alphabetically, so DOCRAY_STRIPE_SECRET_KEY (which also describes the
    # endpoint) precedes this one and would capture everything in between.
    marker = "subscribe it to:"
    end = text.index("DOCRAY_STRIPE_WEBHOOK_SECRET=")
    start = text.rindex(marker, 0, end) + len(marker)
    block = text[start:end]
    event_names: list[str] = []
    for raw in block.replace("\n", " ").split(","):
        event = raw.replace("#", "").replace("and ", "").strip().rstrip(".")
        if event:
            event_names.append(event)
    return event_names


def _production_setup_webhook_events() -> list[str]:
    text = (_MIGRATIONS.parents[1] / "docs" / "production-setup.md").read_text()
    marker = "and select the event types the Billing panel lists as required:"
    start = text.index(marker) + len(marker)
    end = text.index("Put the endpoint signing secret", start)
    block = text[start:end]
    event_names: list[str] = []
    for raw in block.replace("\n", " ").split(","):
        event = raw.replace("`", "").replace("and ", "").strip().rstrip(".")
        if event:
            event_names.append(event)
    return event_names


def _architecture_stripe_section() -> str:
    text = (_MIGRATIONS.parents[1] / "docs" / "architecture.md").read_text()
    marker = "## Stripe billing and payment control plane"
    start = text.index(marker)
    end = text.index("\n## ", start + len(marker))
    return text[start:end]
