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


#: Billing-exempt mutations: the billing control plane itself.
BILLING_EXEMPT_MUTATIONS = {
    "create_billing_checkout_session": "mutation.create_billing_checkout_session",
    "create_billing_portal_session": "mutation.create_billing_portal_session",
    "retry_billing_event": "mutation.retry_billing_event",
}
#: Every other mutation must pass an authorization guard before it enqueues.
BILLING_GUARDED_MUTATIONS = {
    "add_document_to_collection": "mutation.add_document_to_collection",
    "annotate_document": "mutation.annotate",
    "confirm_allusion": "mutation.confirm_allusion",
    "propose_allusion": "mutation.propose_allusion",
    "reject_allusion": "mutation.reject_allusion",
    "archive_document": "mutation.archive_document",
    "bulk_archive_documents": "mutation.bulk_archive_documents",
    "bulk_tag_documents": "mutation.bulk_tag_documents",
    "cancel_job": "mutation.cancel_job",
    "canonicalize_document": "mutation.canonicalize",
    "classify_document": "mutation.classify_document",
    "confirm_document_classification": "mutation.confirm_document_classification",
    "confirm_projection": "mutation.confirm_projection",
    "correct_entity_mention": "mutation.correct_entity_mention",
    "correct_entity_surface": "mutation.correct_entity_surface",
    "create_backup": "mutation.backup",
    "create_collection": "mutation.create_collection",
    "decide_document_metadata_suggestions": ("mutation.decide_document_metadata_suggestions"),
    "decide_visual_captions": "mutation.decide_visual_captions",
    "decide_seeds": "mutation.decide_seeds",
    "delete_collection": "mutation.delete_collection",
    "delete_document": "mutation.delete",
    "demote_lens": "mutation.demote_lens",
    "embed_corpus_sentences": "mutation.embed_corpus_sentences",
    "embed_document_sentences": "mutation.embed_document_sentences",
    "ingest_document": "mutation.ingest",
    "promote_collection_to_lens": "mutation.promote_collection_to_lens",
    "run_operation": "mutation.run_operation",
    "create_operation_schedule": "mutation.create_operation_schedule",
    "update_operation_schedule": "mutation.update_operation_schedule",
    "delete_operation_schedule": "mutation.delete_operation_schedule",
    "reannotate": "mutation.reannotate",
    "reextract_document": "mutation.reextract",
    "reextract_documents": "mutation.reextract_documents",
    "retry_document_extraction": "mutation.retry_extract",
    "reject_projection": "mutation.reject_projection",
    "remove_document_from_collection": "mutation.remove_document_from_collection",
    "restore_backup": "mutation.restore",
    "restore_document": "mutation.restore_document",
    "revoke_document_share": "mutation.revoke_document_share",
    "seed_approved": "mutation.seed_approved",
    "set_document_default_lens": "mutation.set_document_default_lens",
    "set_lens_projects": "mutation.set_lens_projects",
    "semantic_enrich_document": "mutation.semantic_enrich",
    "set_document_visibility": "mutation.set_document_visibility",
    "share_document": "mutation.share_document",
    "suggest_document_metadata": "mutation.suggest_document_metadata",
    "suggest_seeds": "mutation.seed_suggest",
    "update_collection": "mutation.update_collection",
    "update_document_metadata": "mutation.update_metadata",
}
