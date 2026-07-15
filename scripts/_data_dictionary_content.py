"""Hand-authored content constants for the data-dictionary generator.

Split out of ``gen_data_dictionary.py`` (the generation logic) so that file
stays within the D-015 400-net-LOC cap. Pure data — no DB, no logic.
"""

from __future__ import annotations

# Owning-document path used by the RLS predicate for each table (for the dict).
RLS_PATH = {
    "documents": "id (self)",
    "document_content": "document_id",
    "document_authors": "document_id",
    "document_tags": "document_id",
    "document_identifiers": "document_id",
    "document_metadata": "document_id",
    "document_provenance": "document_id",
    "document_collection_members": "document_id",
    "document_pages": "document_id",
    "document_layout_runs": "document_id",
    "provenance_params": "event_id → document_provenance",
    "document_metadata_history": "document_id",
    "document_metadata_suggestion": "document_id",
    "sentences": "document_id",
    "spans": "document_id",
    "annotations": "document_id",
    "coref_chain": "document_id",
    "coref_mention": "span_id → spans",
    "lexeme_occurrences": "annotation_id → annotations",
    "tokens": "annotation_id → annotations",
    "token_feature": "token_id → tokens → annotations",
    "subword": "token_id → tokens → annotations",
    "chunk": "annotation_id → annotations",
    "constituent": "annotation_id → annotations",
    "dependency": "annotation_id → annotations",
    "entity_mention": "annotation_id → annotations",
    "entity_link": "mention_id → entity_mention → annotations",
    "srl_predicate": "annotation_id → annotations",
    "srl_argument": "span_id → spans",
    "word_sense": "annotation_id → annotations",
    "amr_node": "annotation_id → annotations",
    "amr_edge": "parent_node_id → amr_node → annotations",
    "discourse_relation": "annotation_id → annotations",
    "timex": "annotation_id → annotations",
    "event": "annotation_id → annotations",
    "tlink": "annotation_id → annotations",
    "sentiment": "annotation_id → annotations",
    "embedding": "span_id → spans",
    "jobs": "document_id (admin-only when NULL)",
    "seed_suggestion": "document_id",
    "backups": "docray_is_admin()",
    "audit_events": "insert-only security audit log",
    "tenant_billing_accounts": "tenant_id = docray_current_tenant() and docray_is_admin()",
    "tenant_subscriptions": "tenant_id = docray_current_tenant() and docray_is_admin()",
    "stripe_webhook_events": "signed Stripe webhook ledger, app-owned",
}

MIGRATIONS = [
    ("001_init", "documents + document_content (FTS), pg_trgm, updated_at trigger"),
    ("002_sentences", "ulid domain; sentences (ULID PK)"),
    (
        "003_document_entity",
        "bibliographic/lineage columns; provenance_kind enum; document_provenance",
    ),
    ("004_normalize_4nf", "decompose authors/tags/identifiers/metadata/params to relations"),
    ("005_lexemes", "lexemes, annotations, lexeme_occurrences + derived views"),
    ("006_schema_migrations", "the applied-migration ledger"),
    (
        "007_spans",
        "shared span registry; sentences/occurrences onto span_id; layer-aware annotations",
    ),
    ("008_tokens_morphology", "tokens, token_feature (UD FEATS), subword"),
    ("009_syntax", "chunk, constituent, dependency"),
    ("010_reference", "entity_mention, entity_link, coref_chain, coref_mention"),
    ("011_meaning_discourse", "SRL, WSD, AMR, discourse, TimeML, sentiment"),
    ("012_embeddings", "pgvector extension; embedding + HNSW index"),
    ("013_fix_sentence_fk", "tokens/amr_node.sentence_id → ON DELETE SET NULL"),
    ("014_span_fk_cascade", "every span reference → ON DELETE CASCADE"),
    ("015_layer_fk_cascade", "complete the ON DELETE graph for document-owned tables"),
    ("016_fts_language_aware", "trigger-maintained language-aware FTS (en/pt/simple)"),
    (
        "017_schema_review_fixes",
        "typed tlink endpoints; word_sense PK; derived_from SET NULL; catalog comments",
    ),
    ("018_rbac", "document-level RBAC + row-level security on 33 tables"),
    ("019_schema_version", "schema SemVer — schema_version + docray_schema_version()"),
    ("020_jobs", "background jobs queue (text+CHECK status, FOR UPDATE SKIP LOCKED)"),
    ("021_canonical", "canonical entity registry (canonical + HNSW embedding index)"),
    ("022_form_index", "form_index — normalized surface form → canonical lookup"),
    ("023_resolution", "resolution_review + canonical_merge (frontier-resolver review store)"),
    ("024_schema_version_1_3_0", "advance the schema SemVer to 1.3.0"),
    ("025_seed_suggestion", "seed_suggestion — domain-seeding curation queue"),
    ("026_backups", "backups — corpus-snapshot archive catalogue"),
    ("027_fk_cascade_indexes", "index the annotations delete-cascade FK paths"),
    ("028_document_delete_cascade_indexes", "index the document delete-cascade FK paths"),
    ("029_schema_version_1_4_0", "advance the schema SemVer to 1.4.0"),
    ("030_principal_login_roles", "per-principal LOGIN roles and trusted API principal assertion"),
    ("031_schema_version_1_5_0", "advance the schema SemVer to 1.5.0"),
    (
        "032_api_role_runtime_grants",
        "grant API runtime access to jobs and seed suggestions under RLS",
    ),
    ("033_schema_version_1_6_0", "advance the schema SemVer to 1.6.0"),
    ("034_annotation_table_autovacuum", "autovacuum/analyze thresholds for annotation tables"),
    ("035_schema_version_1_7_0", "advance the schema SemVer to 1.7.0"),
    (
        "036_evaluation_drift",
        "evaluation runs, corpus snapshots, metrics, and gold samples",
    ),
    ("037_schema_version_1_8_0", "advance the schema SemVer to 1.8.0"),
    (
        "038_entity_mention_chunk_surface_columns",
        "nullable surface columns on entity_mention and chunk (phase 1 of 038-041)",
    ),
    (
        "039_entity_mention_surface_backfill",
        "backfill entity_mention.surface from document_content.full_text",
    ),
    ("040_chunk_surface_backfill", "backfill chunk.surface from document_content.full_text"),
    (
        "041_entity_mention_chunk_surface_finalize",
        "NOT NULL + indexes on entity_mention/chunk surface after the backfills",
    ),
    ("042_schema_version_1_9_0", "advance the schema SemVer to 1.9.0"),
    ("043_jobs_backups_rls", "Row-Level Security on jobs and backups (BL-064)"),
    ("044_schema_version_1_10_0", "advance the schema SemVer to 1.10.0"),
    (
        "045_document_metadata_history",
        "editable document metadata and its per-field change history (BL-058)",
    ),
    ("046_schema_version_1_11_0", "advance the schema SemVer to 1.11.0"),
    ("047_document_collections", "manual and smart document collections (BL-057)"),
    ("048_schema_version_1_12_0", "advance the schema SemVer to 1.12.0"),
    ("049_span_page_geometry", "persist page/bbox geometry for spans (BL-077)"),
    ("050_schema_version_1_13_0", "advance the schema SemVer to 1.13.0"),
    ("051_document_lifecycle", "document lifecycle state, archive, and soft delete (BL-059)"),
    ("052_schema_version_1_14_0", "advance the schema SemVer to 1.14.0"),
    (
        "053_document_lifecycle_history_constraint",
        "allow lifecycle_state transitions in document metadata history",
    ),
    (
        "054_document_layout_run_page_dimensions",
        "repair document_layout_runs page dimensions for already-migrated databases",
    ),
    (
        "055_document_metadata_suggestions",
        "verified document metadata suggestions for human review (BL-061)",
    ),
    ("056_schema_version_1_15_0", "advance the schema SemVer to 1.15.0"),
    (
        "057_job_cancel_update_policy",
        "admin-only cancellation update policy for queued/running jobs (BL-102)",
    ),
    ("058_schema_version_1_16_0", "advance the schema SemVer to 1.16.0"),
    (
        "059_job_progress",
        "first-class async job progress fields on jobs (BL-123)",
    ),
    ("060_schema_version_1_17_0", "advance the schema SemVer to 1.17.0"),
    (
        "061_tenant_foundation",
        "tenant catalogue plus tenant-scoped principals, documents, and RLS helpers",
    ),
    (
        "062_security_audit_events",
        "append-only security audit log for auth, reads, mutations, and denials",
    ),
    (
        "063_audit_events_admin_read",
        "tenant-admin SELECT policy and grants for audit-event search/export",
    ),
    (
        "064_abac_sensitive_document_policy",
        "ABAC sensitive-document write policy helper and explicit sensitive grant",
    ),
    (
        "065_scim_provisioning",
        "SCIM-managed principals, groups, and authoritative role-edit guard (BL-068)",
    ),
    ("066_schema_version_1_18_0", "advance the schema SemVer to 1.18.0"),
    (
        "067_saml_identity_providers",
        "tenant-scoped SAML IdP configuration and assertion-consumer settings (BL-067)",
    ),
    ("068_schema_version_1_19_0", "advance the schema SemVer to 1.19.0"),
    (
        "069_document_pages",
        "per-page PDF geometry rows, including text-free pages (BL-126)",
    ),
    ("070_schema_version_1_20_0", "advance the schema SemVer to 1.20.0"),
    ("071_schema_version_1_21_0", "advance the schema SemVer to 1.21.0"),
    (
        "072_jobs_drain_requested_at",
        "jobs.drain_requested_at: deploy-kill of a draining worker releases the job "
        "without spending an attempt (BL-132)",
    ),
    ("073_schema_version_1_22_0", "advance the schema SemVer to 1.22.0"),
    (
        "074_local_auth",
        "local self-service auth: principal_credentials (argon2id), "
        "email_verification_tokens, auth_refresh_tokens",
    ),
    ("075_schema_version_1_23_0", "advance the schema SemVer to 1.23.0"),
    (
        "076_reader_corpus_read",
        "docray_readable_documents() gains the role-permission read branch (reader "
        "role sees the tenant corpus) excluding sensitivity=sensitive documents",
    ),
    ("077_schema_version_1_24_0", "advance the schema SemVer to 1.24.0"),
    (
        "078_drop_duplicate_spans_index",
        "drop idx_spans_document, the redundant duplicate of the "
        "spans(document_id, char_start, char_end) unique index",
    ),
    (
        "079_account_settings",
        "self-service account settings and email-change request storage",
    ),
    (
        "080_billing",
        "tenant Stripe billing accounts, subscription snapshots, and webhook ledger",
    ),
    ("081_schema_version_1_25_0", "advance the schema SemVer to 1.25.0"),
    (
        "082_oauth_clients",
        "OAuth 2.1 dynamic clients and authorization-code/PKCE state",
    ),
    (
        "083_billing_event_read",
        "tenant-admin read policy for Stripe webhook payment activity",
    ),
    ("084_schema_version_1_26_0", "advance the schema SemVer to 1.26.0"),
    (
        "085_tenant_catalog_client_select",
        "repair client-role tenant catalogue read access for billing status",
    ),
    ("086_schema_version_1_27_0", "advance the schema SemVer to 1.27.0"),
    (
        "087_oauth_client_service_principal",
        "bind confidential OAuth clients to explicit service principals for client_credentials",
    ),
    (
        "088_billing_event_retry",
        "tenant-admin retry policy for unprocessed Stripe billing webhook events",
    ),
]

# ---------------------------------------------------------------------------
# Curated metadata: how tables group into layers, and a one-line purpose each.
# (Table shapes/columns come from the catalog; this is just narrative + order.)
# ---------------------------------------------------------------------------

GROUPS: list[tuple[str, str, list[str]]] = [
    (
        "Document entity & bibliographic identity",
        "The document is a first-class entity. Independent multivalued facts "
        "(authors, tags, identifiers, format metadata) are decomposed to their own "
        "relations (4NF); blobs live in object storage, only an `s3://` pointer + "
        "sha256 are stored.",
        [
            "documents",
            "document_content",
            "authors",
            "document_authors",
            "tags",
            "document_tags",
            "document_identifiers",
            "document_metadata",
            "document_pages",
            "document_layout_runs",
        ],
    ),
    (
        "Provenance (append-only lineage)",
        "Every processing/lineage event on a document, with its parameters "
        "normalized out of a JSON bag into `(event, key) -> value`. "
        "`document_metadata_history` is the analogous append-only trail for "
        "editable title/description/tags edits (BL-058) — a distinct concern "
        "from pipeline/ingest events, RLS-scoped and read-granted the same way.",
        ["document_provenance", "provenance_params", "document_metadata_history"],
    ),
    (
        "Segmentation & the span registry",
        "Character offsets are a first-class, addressable entity (`spans`); every "
        "stand-off annotation references a span by FK rather than repeating offset "
        "triples. Sentences are ULID-keyed and reference a span.",
        ["spans", "sentences"],
    ),
    (
        "Lexeme & annotation backbone",
        "`lexemes` is analyzer-independent type identity; `annotations` is one "
        "analyzer run over a document scoped by `(layer, tool)`; `lexeme_occurrences` "
        "is the materialized projection of token-level lexeme links that the "
        "webapp/API render for lexeme and KWIC reads.",
        ["lexemes", "annotations", "lexeme_occurrences"],
    ),
    (
        "Tokens, morphology & subwords (NLP layers 3–7)",
        "The token is the addressable unit syntax/morphology reference. UD FEATS and "
        "BPE/WordPiece pieces decompose into child relations (no arrays, no pipe-strings).",
        ["tokens", "token_feature", "subword"],
    ),
    (
        "Syntax — shallow & deep (layers 8–10)",
        "Chunking (BIO collapsed to spans), constituency (adjacency-list tree), and "
        "dependency (each token has exactly one head → a function).",
        ["chunk", "constituent", "dependency"],
    ),
    (
        "Entities & reference (layers 11–13)",
        "Named-entity mentions, entity linking to a KB, and coreference chains.",
        ["entity_mention", "entity_link", "coref_chain", "coref_mention"],
    ),
    (
        "Canonical entities & resolution",
        "The tenant-global canonical entity registry mentions link to: `canonical` "
        "rows carry KB identity + an HNSW-indexed name embedding, `form_index` maps "
        "normalized surface forms to canonicals, `canonical_merge` is the append-only "
        "merge audit, and `seed_suggestion`/`resolution_review` are the operator "
        "workflow queues for seeding and ambiguous-link review.",
        [
            "canonical",
            "form_index",
            "canonical_merge",
            "seed_suggestion",
            "resolution_review",
        ],
    ),
    (
        "Meaning, discourse, temporal & sentiment (layers 14–19)",
        "Semantic role labeling, word-sense disambiguation, AMR meaning graphs, "
        "PDTB discourse relations, TimeML (TIMEX/EVENT/TLINK), and sentiment.",
        [
            "srl_predicate",
            "srl_argument",
            "word_sense",
            "amr_node",
            "amr_edge",
            "discourse_relation",
            "timex",
            "event",
            "tlink",
            "sentiment",
        ],
    ),
    (
        "Distributed representations (layer 20)",
        "pgvector embeddings over spans, ANN-indexed with HNSW (cosine).",
        ["embedding"],
    ),
    (
        "Collections & curation",
        "Operator-facing curation surfaces: manual and smart (rule-driven) document "
        "collections, and the reviewed-suggestion queue for LLM/heuristic metadata "
        "proposals (BL-058).",
        [
            "document_collections",
            "document_collection_members",
            "document_metadata_suggestion",
        ],
    ),
    (
        "Access control — RBAC & row-level security",
        "Principals, roles, permissions and per-document ACLs. Every document-scoped "
        "table above is protected by tenant-scoped RLS keyed on these (see §Access "
        "control).",
        [
            "tenants",
            "principals",
            "roles",
            "permissions",
            "role_permissions",
            "principal_roles",
            "document_acl",
        ],
    ),
    (
        "Authentication & identity provisioning",
        "Local self-service auth (argon2id credentials, e-mail verification, refresh "
        "tokens — migration 074), account preferences and email-change requests, "
        "OAuth 2.1 authorization-code/PKCE client state, plus enterprise identity: "
        "SCIM-provisioned groups and tenant-scoped SAML identity providers. Admin "
        "stays operator-seeded; self-registered principals get the reader role only.",
        [
            "principal_credentials",
            "email_verification_tokens",
            "auth_refresh_tokens",
            "principal_preferences",
            "email_change_requests",
            "oauth_clients",
            "oauth_authorization_codes",
            "scim_groups",
            "scim_group_members",
            "tenant_saml_identity_providers",
        ],
    ),
    (
        "Billing & payment operations",
        "Tenant-scoped Stripe customer accounts, current subscription snapshots, "
        "and the signed webhook event ledger that backs Checkout, Portal, "
        "entitlement decisions, and payment audit views.",
        [
            "tenant_billing_accounts",
            "tenant_subscriptions",
            "stripe_webhook_events",
        ],
    ),
    (
        "Security audit trail",
        "Append-only access and authentication events: who did what, against which "
        "target, from which source address, and whether it succeeded or was denied.",
        ["audit_events"],
    ),
    (
        "Evaluation & drift artifacts",
        "Corpus snapshots, evaluation runs, metric rows, and gold samples make quality and drift "
        "measurements durable and citeable instead of one-off operator observations.",
        ["corpus_snapshot", "evaluation_run", "evaluation_metric", "gold_sample"],
    ),
    (
        "Jobs & operations",
        "The background job queue (claimed FOR UPDATE SKIP LOCKED, heartbeat + "
        "drain-aware reaping, first-class progress), the applied-migration ledger, "
        "the schema's SemVer, and the corpus-backup catalogue. `backups` and the "
        "`jobs` queue are excluded from every snapshot and from restore, so a "
        "restore never clobbers the queue or the archive index it is reading from.",
        ["jobs", "schema_migrations", "schema_version", "backups"],
    ),
]
