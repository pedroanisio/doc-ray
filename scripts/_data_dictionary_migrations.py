"""Migration narrative for the data-dictionary generator (§9 MIGRATIONS).

Split out of ``_data_dictionary_content.py`` so that file stays within the D-015
400-net-LOC cap, exactly as ``_data_dictionary_groups.py`` was split out for the
§5 table grouping. Pure data — no DB, no logic: one ``(stem, purpose)`` pair per
file in ``db/migrations``, in applied order. ``tests/test_docs_in_sync.py`` fails
if a migration file has no entry here.
"""

from __future__ import annotations

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
    ("089_visual_tier1", "Prism Tier-1: document_image + image_tier1_features (deterministic)"),
    ("090_schema_version_1_28_0", "advance the schema SemVer to 1.28.0"),
    ("091_visual_tier2", "Prism Tier-2: image_embedding + sentence_vision_embedding (HNSW)"),
    ("092_schema_version_1_29_0", "advance the schema SemVer to 1.29.0"),
    ("093_visual_tier3", "Prism Tier-3: visual_caption + visual_review (grounded VLM)"),
    ("094_schema_version_1_30_0", "advance the schema SemVer to 1.30.0"),
    ("095_corpus_stats", "corpus stat-band rollups (D-024) — kill the 16s landing query"),
    ("096_schema_version_1_31_0", "advance the schema SemVer to 1.31.0"),
    ("097_canonical_client_read", "grant the API/webapp role SELECT on the canonical registry"),
    ("098_reader_stats", "reader-page rollups — stop aggregating lexeme_occurrences on read"),
    ("099_schema_version_1_32_0", "advance the schema SemVer to 1.32.0"),
    ("100_read_model_token_entity", "extend the read-model rollups with token + entity counts"),
    ("101_schema_version_1_33_0", "advance the schema SemVer to 1.33.0"),
    (
        "102_document_ownership",
        "documents.owner_principal_id becomes the read boundary (supersedes 076)",
    ),
    ("103_schema_version_1_34_0", "advance the schema SemVer to 1.34.0"),
    ("104_member_role", "the `member` role — a registered user who can own and curate a corpus"),
    ("105_schema_version_1_35_0", "advance the schema SemVer to 1.35.0"),
    ("106_force_rls", "FORCE ROW LEVEL SECURITY on every RLS table + the docray_batch writer role"),
    ("107_schema_version_1_36_0", "advance the schema SemVer to 1.36.0"),
    ("108_document_sharing", "owner-driven document sharing: grants + FORCE RLS on document_acl"),
    ("109_schema_version_1_37_0", "advance the schema SemVer to 1.37.0"),
    ("110_org_corpus", "organization corpus: documents.visibility + org-read branch"),
    ("111_schema_version_1_38_0", "advance the schema SemVer to 1.38.0"),
    ("112_web_auth_sessions", "durable webapp auth sessions keyed by an opaque cookie id"),
    ("113_jobs_trusted_writer_enqueue", "jobs enqueue moves behind a definer (BL-141)"),
    ("114_rls_helper_bypass_owner", "SECURITY DEFINER RLS helpers run with BYPASSRLS (BL-142)"),
    ("115_ner_corrections", "durable human NER corrections keyed by a stable span"),
    ("116_schema_version_1_39_0", "advance the schema SemVer to 1.39.0"),
    ("117_form_index_client_read", "grant docray_client SELECT on form_index (BL-146 fix)"),
    ("118_metadata_history_authors_field", "document metadata history admits the authors field"),
    ("119_backup_role_default_grants", "docray_batch default-privilege grants for backups"),
    ("120_schema_version_1_40_0", "advance the schema SemVer to 1.40.0"),
    (
        "121_lens_registry",
        "Lens Registry: is_scope/parent_lens_id/default_lens_id + global lens",
    ),
    ("122_schema_version_1_41_0", "advance the schema SemVer to 1.41.0"),
    (
        "123_resolution_review_client_read",
        "grant + RLS on resolution_review so client reads (resolutionReview) work (#15 sweep)",
    ),
    (
        "124_semantic_embedding_cutover",
        "NLP Layer 20 hard cutover: purge hash-bow-768 and advance SemVer to 1.42.0",
    ),
    ("125_schema_version_1_43_0", "advance the schema SemVer to 1.43.0 for epic #23"),
    ("126_job_telemetry", "append-only per-job execution telemetry for cost attribution"),
    (
        "127_document_classification",
        "document_classification table (genre axis + resolution-policy input) + RLS + grants",
    ),
    ("128_schema_version_1_45_0", "advance the schema SemVer to 1.45.0 for genre classification"),
    (
        "129_entity_projection",
        "projection engine: entity_projection + lens_local_entity + "
        "entity_projection_review + lens_entity_stats + collections.projects col (RLS + grants)",
    ),
    ("130_schema_version_1_46_0", "advance the schema SemVer to 1.46.0 for the projection engine"),
    (
        "131_allusion_edges",
        "entity_allusion (typed cross-lens links that never assert identity) "
        "+ cross-lens RLS + grants",
    ),
    ("132_schema_version_1_47_0", "advance the schema SemVer to 1.47.0 for allusion edges"),
    (
        "133_lens_rollups",
        "hyper-context rollups: lens_entity_rollup + lens_entity_relation "
        "(co-occurrence NPMI) + RLS; supersedes/drops lens_entity_stats",
    ),
    ("134_schema_version_1_48_0", "advance the schema SemVer to 1.48.0 for hyper-context rollups"),
    ("135_schema_version_1_48_1", "advance the schema SemVer to 1.48.1 (marker, no DDL)"),
    (
        "136_schema_version_1_48_2",
        "advance the schema SemVer to 1.48.2 for the arXiv source adapter and ingest "
        "fixes (marker, no DDL)",
    ),
    (
        "137_schema_version_1_49_0",
        "advance the schema SemVer to 1.49.0 for reference intake across every "
        "surface (marker, no DDL)",
    ),
    (
        "138_mention_confidence",
        "nullable explainable confidence materialization on entity_mention",
    ),
    (
        "139_schema_version_1_50_0",
        "advance the schema SemVer to 1.50.0 for mention-confidence review",
    ),
    (
        "140_entity_surface_corrections",
        "durable exact-surface/current-type NER correction rules without fake spans",
    ),
    (
        "141_schema_version_1_51_0",
        "advance the schema SemVer to 1.51.0 for exact-surface NER corrections",
    ),
    (
        "142_corpus_stats_sentences",
        "corpus_stats.n_sentences — the tenant sentence total the term-frequency "
        "sentence scan divides by, rolled up from document_stats (never counted live)",
    ),
    (
        "143_schema_version_1_52_0",
        "advance the schema SemVer to 1.52.0 for the measured phrase pre-flight",
    ),
    (
        "144_operation_schedule",
        "operation_schedule — recurring corpus-maintenance operations (visual tiers, "
        "semantic embed, geometry re-extract) with a materialized next_run_at the "
        "scheduler claims with FOR UPDATE SKIP LOCKED; admin-only, no user write policy",
    ),
    (
        "145_schema_version_1_53_0",
        "advance the schema SemVer to 1.53.0 for the read/curation MCP endpoint split",
    ),
    (
        "146_corpus_stats_sentence_denominator",
        "repair the full corpus-stats refresh, backfill sentence denominators, and "
        "assert the trusted batch-owner invariant",
    ),
    (
        "147_schema_version_1_53_1",
        "advance the schema SemVer to 1.53.1 for the honest sentence denominator",
    ),
    (
        "148_readable_sentence_ids",
        "add the SECURITY DEFINER candidate helper that lets sentence-text search "
        "reach its trigram index again: RLS forbids a non-leakproof ILIKE ahead of "
        "its security qual, so the index has been unreachable since migration 018",
    ),
    (
        "149_schema_version_1_54_0",
        "advance the schema SemVer to 1.54.0 for the index-backed sentence search",
    ),
    (
        "150_caption_retraction",
        "add visual_caption.retracted_at / retracted_by / retraction_reason and a "
        "partial index over them: the Tier-3 flow inverts to approve-on-write, so a "
        "retraction becomes the human verification event and has to carry the same "
        "who/when/why audit trail the pre-publication approval used to",
    ),
    (
        "151_schema_version_1_55_0",
        "advance the schema SemVer to 1.55.0 for caption retraction",
    ),
    (
        "152_corpus_stats_total_lexeme_uses",
        "add corpus_stats.total_lexeme_uses and maintain it in the refresh "
        "functions: term_frequency derived its share denominator as a live "
        "SUM(uses) over lexeme_stats, which forced RLS evaluates per row across "
        "444k rows — 22.9 s on the real API path against a 15 s statement "
        "timeout, so every call failed. The same fix migration 142 applied to "
        "the sentence denominator, and additive, so a rolling deploy reads old "
        "rows unchanged",
    ),
    (
        "153_schema_version_1_56_0",
        "advance the schema SemVer to 1.56.0 for the maintained term-frequency denominator",
    ),
    (
        "154_incremental_corpus_rollups",
        "maintain the tenant rollups by delta instead of rebuilding them: "
        "docray_refresh_stats_for_document deleted and regrouped lexeme_stats "
        "over the 72.7M-row lexeme_occurrences table six times an hour, leaving "
        "both rollups ~99% dead tuples. Per-document snapshots in "
        "document_lexeme_stats / document_entity_frequency_stats now carry only "
        "the difference into the tenant rollup, a BEFORE DELETE trigger "
        "withdraws a deleted document's contribution, and corpus_stats is "
        "stamped after the rollups it summarises",
    ),
    (
        "155_schema_version_1_57_0",
        "advance the schema SemVer to 1.57.0 for the incremental corpus rollups",
    ),
    (
        "156_backfill_total_lexeme_uses",
        "backfill corpus_stats.total_lexeme_uses through a SECURITY DEFINER "
        "function owned by docray_batch: migration 152 ended with a bare UPDATE, "
        "which wrote nothing and raised nothing because corpus_stats has FORCE "
        "ROW LEVEL SECURITY with no UPDATE policy and the migration role is not "
        "BYPASSRLS, so the column shipped NULL against a true 72,681,387. "
        "docray_backfill_total_lexeme_uses() is re-runnable after a restore",
    ),
    (
        "157_embedding_read_policy_plan_shape",
        "rewrite the embedding p_read policy so a corpus-wide vector search is "
        "planned as a per-row primary-key probe: expressing visibility as "
        "span_id IN (SELECT id FROM spans WHERE document_id IN readable) makes "
        "the planner derive ~28M span ids out of 113M rows in spans, too large "
        "to hash, so it materialised the set and rescanned it per candidate row "
        "and every corpus-wide semantic_search_sentences call hit the statement "
        "timeout. EXISTS is not enough — the planner un-nests it to a semi-join "
        "— so the rule is a correlated scalar subquery on spans.id against a "
        "once-materialised readable-document array. Same visibility (spans.id is "
        "unique), >90 s to 0.20 s, no table rewritten",
    ),
    (
        "158_schema_version_1_57_1",
        "advance the schema SemVer to 1.57.1 for the executable embedding read policy",
    ),
    (
        "159_grant_rollup_helpers_to_batch",
        "grant EXECUTE on the three per-document rollup helpers to docray_batch: "
        "migration 154 created docray_refresh_document_lexeme_stats, "
        "docray_refresh_document_entity_frequency_stats and "
        "docray_unapply_document_rollups owned by docray, revoked ALL from "
        "PUBLIC, and never granted the role that owns their only caller. "
        "docray_refresh_stats_for_document is SECURITY DEFINER owned by "
        "docray_batch, so its body runs as docray_batch and could not execute "
        "them — failing for every caller, docray included, because the definer "
        "switches identity before reaching the inner call. The driver aborted at "
        "that line, so the corpus_stats refresh after it never ran either, and "
        "154's delta path had already replaced the full rebuild: lexeme_stats, "
        "entity_frequency_stats and the corpus totals derived from them silently "
        "stopped tracking the corpus while ingestion logged one warning per "
        "document. Run docray_refresh_all_corpus_stats() once after applying",
    ),
    (
        "160_schema_version_1_57_2",
        "advance the schema SemVer to 1.57.2 for the executable rollup driver",
    ),
    (
        "161_schema_version_1_57_3",
        "advance the schema SemVer to 1.57.3 — Layer 9 constituents keep their "
        "own head token, so spans written before it may understate their start",
    ),
    (
        "162_rollup_helpers_owned_by_batch",
        "assign the three per-document rollup helpers from migration 154 to "
        "docray_batch, verify the batch-owner/RLS invariant, and rebuild every "
        "tenant's rollups once: SECURITY DEFINER runs as the owner, and owned by "
        "docray (not BYPASSRLS) each helper read its inputs through forced RLS with "
        "no principal, saw zero rows, computed an empty delta and wrote nothing — "
        "raising nothing while the driver stamped corpus_stats fresh",
    ),
    (
        "163_schema_version_1_57_4",
        "advance the schema SemVer to 1.57.4 — the per-document rollup helpers run as docray_batch",
    ),
]
