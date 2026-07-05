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
    "provenance_params": "event_id → document_provenance",
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
]
