"""Curated §5 table groups for the data dictionary generator.

Split out of ``_data_dictionary_content.py`` to stay within the D-015 cap
(400 net LOC): that module now carries the RLS map and the migration
history, this one the layer grouping. ``gen_data_dictionary.py`` imports
``GROUPS`` from ``_data_dictionary_content`` either way — the re-export
there keeps the generator's import surface unchanged.
"""

from __future__ import annotations

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
        "Named-entity mentions, entity linking to a KB, and coreference chains. "
        "`entity_mention` is rebuilt from scratch by every annotate run, so human "
        "fixes live in `entity_mention_correction` — keyed by a stable span, "
        "re-applied by the pipeline on each pass (Block A).",
        [
            "entity_mention",
            "entity_mention_correction",
            "entity_link",
            "coref_chain",
            "coref_mention",
        ],
    ),
    (
        "Genre classification (SLP Block C)",
        "A document's genre axis — fiction / non_fiction / mixed / unknown — "
        "orthogonal to its file format (`documents.doc_type`). A heuristic classifier "
        "writes a `pending` guess; a human confirms it (`method='human'`). This is "
        "the input to the per-entity-type resolution policy "
        "(`doc_ray.domain.genre_policy`): fictional types resolve inside the work's "
        "lens, real-world types globally. The policy is defined here; Block D (#8) "
        "applies it.",
        ["document_classification"],
    ),
    (
        "Projection engine (SLP Block D)",
        "Lens-scoped entity identity as a derived, refreshable view: "
        "`entity_projection` is one row per (mention, projecting lens) holding "
        "`project(mention, lens)` — `scope` local/global, a nullable `canonical_id` "
        "(NULL = explicitly unresolved), and a `proposed`/`confirmed`/`rejected` "
        "review status. `lens_local_entity` is the stable registry that mints one "
        "`canonical` row per (lens, surface, type) so a rebuild is reproducible; "
        "`entity_projection_review` records human confirm/reject keyed by the stable "
        "span (survives re-annotation); `lens_entity_stats` is the per-lens entity "
        "rollup `lensEntities` reads (D-024). A lens projects only when "
        "`document_collections.projects` is set (FR-2 designation). All are derived "
        "from the substrate + lens + policy and never mutate it (invariants 1–2).",
        [
            "entity_projection",
            "lens_local_entity",
            "entity_projection_review",
            "lens_entity_stats",
        ],
    ),
    (
        "Allusion edges (SLP Block E)",
        "Typed, directional cross-lens links that record a relationship without asserting "
        "identity: `entity_allusion` says one entity *alludes to / depicts / references / "
        "is adapted from / inspired by* another (e.g. a novel's Paris → the real Paris) "
        "across lenses, carrying a confidence, method, review status, and evidence "
        "document. An edge changes NOTHING about projection or canonical identity "
        "(invariant 2) and is never transitive. A curation table the API writes directly "
        "(docray_client); machine-proposed edges land `proposed` for review. Its RLS is "
        "cross-lens: an edge is visible only when the caller can see BOTH endpoint lenses "
        "and the evidence document, so it never reveals an entity in an unreadable lens.",
        ["entity_allusion"],
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
            "web_auth_sessions",
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
        "restore never clobbers the queue or the archive index it is reading from. "
        "`job_telemetry` is an append-only per-job execution record (GPU/CPU lane, "
        "timings) for cost attribution (migration 126).",
        ["jobs", "schema_migrations", "schema_version", "backups", "job_telemetry"],
    ),
    (
        "Prism visual layer",
        "The visual/multimodal layer over the corpus (docs/proposals/prism-visual-"
        "layer.md), document-scoped under the same RLS as the NLP layers. Tier-1 "
        "`document_image`/`image_tier1_features` are deterministic (no model). Tier-2 "
        "`image_embedding`/`sentence_vision_embedding` are HNSW-indexed vectors in one "
        "shared space for cross-modal retrieval (ranking only). Tier-3 `visual_caption` "
        "holds grounded VLM captions (only status='committed' is trusted) and "
        "`visual_review` is the human gate for low-confidence output.",
        [
            "document_image",
            "image_tier1_features",
            "image_embedding",
            "sentence_vision_embedding",
            "visual_caption",
            "visual_review",
        ],
    ),
    (
        "Read-model rollups (D-024)",
        "Derived aggregates over the large append-mostly fact tables, refreshed by "
        "the trusted batch writer at the pipeline job boundary and never computed on "
        "the request path (migrations 095/098/100). Each rollup carries its own RLS "
        "grain: `document_stats` is document-scoped and inherits the document read "
        "policy, while `corpus_stats`, `lexeme_stats`, and `entity_frequency_stats` "
        "are tenant-scoped — one row per tenant rather than one global row, which "
        "would leak vocabulary and entity magnitude across tenants.",
        [
            "document_stats",
            "corpus_stats",
            "lexeme_stats",
            "entity_frequency_stats",
        ],
    ),
]
