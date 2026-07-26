"""Truncate the corpus for a fresh set of uploads (``make truncate-db``).

Wipes documents, every derived annotation/lexeme/evaluation layer, and the
job queue, while preserving what a fresh corpus still needs:

* schema bookkeeping (``schema_migrations``, ``schema_version``)
* auth config (``principals``/``roles``/``permissions`` and join tables), so
  configured ``DOCRAY_API_TOKENS`` principal mappings keep working
* tenant and IdP-managed provisioning state (``tenants``, SCIM groups, SAML
  identity-provider config), so corpus resets do not deconfigure access
* append-only security audit history (``audit_events``)
* the curated canonical registry (``canonical``, ``canonical_merge``,
  ``form_index``), so new documents canonicalize against existing curation
* the ``backups`` catalog (existing archives stay restorable)

Object-store blobs are deliberately untouched: re-uploads are
content-addressed and idempotent, and the bucket also holds backup archives.

Refuses to run without ``--yes``. Take a safety dump first — ``make db-dump``
does — because this is irreversible without one.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

#: Corpus + derived + queue tables emptied by the wipe. TRUNCATE without
#: CASCADE is intentional: if a future migration adds a table referencing any
#: of these, Postgres refuses until it is classified here — a fail-closed
#: prompt to decide preserve-vs-wipe, enforced by test_truncate_corpus.py.
WIPED_TABLES = (
    "documents",
    "document_content",
    "document_acl",
    "document_classification",
    "document_authors",
    "document_identifiers",
    "document_layout_runs",
    "document_metadata",
    "document_metadata_history",
    "document_metadata_suggestion",
    "document_pages",
    "document_provenance",
    "provenance_params",
    "document_tags",
    "document_collection_members",
    "document_collections",
    "authors",
    "tags",
    "annotations",
    "tokens",
    "token_feature",
    "subword",
    "spans",
    "sentences",
    "dependency",
    "constituent",
    "coref_chain",
    "coref_mention",
    "discourse_relation",
    "embedding",
    "entity_link",
    "entity_mention",
    "entity_mention_correction",
    # Projection engine (Block D, #8): derived per-document interpretation + the
    # human review log keyed to it. Wiped with the mentions/documents they project.
    "entity_projection",
    "entity_projection_review",
    # Allusion edges (Block E, #9): cross-lens curation anchored to document_collections
    # (the lenses), which are WIPED — an edge to/from a wiped lens is meaningless, and
    # classifying it preserved would break the FK-consistency gate. So it is wiped too.
    "entity_allusion",
    "event",
    "sentiment",
    "srl_argument",
    "srl_predicate",
    "timex",
    "tlink",
    "word_sense",
    "amr_edge",
    "amr_node",
    "chunk",
    "lexeme_occurrences",
    "lexemes",
    # D-024 read-model rollups (migrations 095/098/100): precomputed aggregates
    # over the corpus tables above. They are derived data, not facts of their
    # own, so they are wiped with what they summarise — leaving them behind
    # would have the reader and the corpus band reporting a corpus that is gone.
    "document_stats",
    "corpus_stats",
    "lexeme_stats",
    "entity_frequency_stats",
    # Block D (#8) local-identity registry and Block F (#10) hyper-context rollups —
    # derived from the projections above (and referencing the WIPED document_collections),
    # so they are wiped with the corpus and rebuilt on the next refresh.
    "lens_local_entity",
    "lens_entity_rollup",
    "lens_entity_relation",
    "seed_suggestion",
    "resolution_review",
    # Prism visual layer — derived per-document corpus data, wiped with the corpus.
    "visual_review",
    "visual_caption",
    "image_embedding",
    "sentence_vision_embedding",
    "image_tier1_features",
    "document_image",
    "corpus_snapshot",
    "evaluation_run",
    "evaluation_metric",
    "gold_sample",
    "jobs",
)

#: Tables that must survive a corpus wipe (see the module docstring).
PRESERVED_TABLES = (
    "schema_migrations",
    "schema_version",
    "principals",
    "principal_roles",
    "roles",
    "permissions",
    "role_permissions",
    "tenants",
    "scim_groups",
    "scim_group_members",
    "tenant_saml_identity_providers",
    # Local-auth account state (migrations 074/076): credentials and pending
    # email verifications belong to principals, and refresh-token rotation
    # state is session continuity — a corpus wipe must not delete accounts or
    # force every user to log in again.
    "principal_credentials",
    "email_verification_tokens",
    "auth_refresh_tokens",
    # Durable webapp sessions (migration 112): same rationale as refresh tokens —
    # a corpus wipe must not sign every logged-in user out.
    "web_auth_sessions",
    # Account settings and pending address changes (migration 079) belong to the
    # principal, not the corpus.
    "principal_preferences",
    "email_change_requests",
    # OAuth registrations and in-flight authorization codes (migration 082) are
    # connector infrastructure; wiping them would break every registered client.
    "oauth_clients",
    "oauth_authorization_codes",
    # Billing state (migration 080) mirrors the payment provider. A corpus wipe
    # must never desynchronise a live subscription or replay a processed webhook.
    "tenant_billing_accounts",
    "tenant_subscriptions",
    "stripe_webhook_events",
    "audit_events",
    "canonical",
    "canonical_merge",
    "form_index",
    "backups",
    # Per-job cost telemetry (migration 126) is append-only and deliberately keyed
    # by a non-FK job_id so it outlives the job it describes; it is cost-attribution
    # history, not corpus content, so a corpus wipe must not erase it.
    "job_telemetry",
)


def truncate_corpus(conn: psycopg.Connection) -> None:
    """Empty every table in :data:`WIPED_TABLES` in one statement."""
    statement = sql.SQL("TRUNCATE TABLE {}").format(
        sql.SQL(", ").join(sql.Identifier(t) for t in WIPED_TABLES)
    )
    conn.execute(statement)


def _counts(conn: psycopg.Connection, tables: tuple[str, ...]) -> dict[str, int]:
    out = {}
    for table in tables:
        row = conn.execute(
            sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))
        ).fetchone()
        out[table] = int(row["n"]) if row else 0
    return out


def main(argv: list[str] | None = None) -> int:
    """Run the wipe from the CLI; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Actually truncate (required).")
    args = parser.parse_args(argv)
    if not args.yes:
        print("Refusing to truncate without --yes. This empties the corpus irreversibly.")
        print(f"Would wipe {len(WIPED_TABLES)} tables; preserves {', '.join(PRESERVED_TABLES)}.")
        return 1

    dsn = os.environ.get("DATABASE_URL", "postgresql://docray:docray@localhost:5455/docray")
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        truncate_corpus(conn)
        wiped = _counts(conn, ("documents", "jobs", "sentences", "lexemes"))
        kept = _counts(conn, ("canonical", "principals", "backups"))
    for table, n in wiped.items():
        print(f"wiped   {table}: {n}")
    for table, n in kept.items():
        print(f"kept    {table}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
