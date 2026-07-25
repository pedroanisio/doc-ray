"""Target-scale table instantiations for the doc-ray catalog.

Split out of :mod:`capacity_model.schema_catalog` purely for the D-015
per-file size gate: the dataclasses, corpus profile, and cardinality ratios
live there; this module holds the one large literal — the ~17 modeled tables
with their exact column shapes and index definitions, each traceable to its
migration (citations in the schema_catalog module docstring and inline).
"""

from __future__ import annotations

from capacity_model.assumptions import AssumptionRegistry, Epistemic, Source
from capacity_model.schema_catalog import (
    ULID_BYTES,
    CorpusProfile,
    IndexSpec,
    TableSpec,
    annotation_ratios,
)
from capacity_model.storage import Column


def build_tables(reg: AssumptionRegistry, corpus: CorpusProfile) -> list[TableSpec]:
    """Instantiate the capacity-dominating tables at target-scale row counts."""
    r = annotation_ratios(reg)
    ann_s = corpus.annotated_sentences
    tokens = ann_s * corpus.tokens_per_sentence
    uuid_c, ulid_c = "uuid", "text"
    text = Column
    specs = [
        TableSpec(
            "tokens",
            tokens,
            [
                text("id", ulid_c, ULID_BYTES),
                text("annotation_id", ulid_c, ULID_BYTES),
                text("span_id", uuid_c),
                text("sentence_id", ulid_c, ULID_BYTES, null_frac=0.05),
                text("ordinal", "integer"),
                text("lexeme_uid", "text", 20, null_frac=0.3),
                text("upos", "text", 4, null_frac=0.05),
                text("xpos", "text", 3, null_frac=0.05),
            ],
            [
                IndexSpec("tokens_pkey", ULID_BYTES),
                IndexSpec("tokens_annotation_span_key", ULID_BYTES + 16),
                IndexSpec("idx_tokens_annotation", ULID_BYTES + 4, dedup_factor=0.7),
                IndexSpec("idx_tokens_span", 16),
                IndexSpec("idx_tokens_lexeme", 20, dedup_factor=0.4),
                IndexSpec("idx_tokens_sentence", ULID_BYTES, dedup_factor=0.5),
            ],
            autovacuum_override=True,
            rls_hops=1,
            reannotate_rows_per_doc=corpus.sentences_per_doc * corpus.tokens_per_sentence,
        ),
        TableSpec(
            "spans",
            ann_s
            * r["ratio.spans_per_sentence"]
            / max(corpus.annotated_fraction, 1e-9)
            * corpus.annotated_fraction,
            [
                text("id", uuid_c),
                text("document_id", uuid_c),
                text("char_start", "integer"),
                text("char_end", "integer"),
                text("page_number", "integer", null_frac=0.7),
                text("bbox_x0", "double precision", null_frac=0.7),
                text("bbox_y0", "double precision", null_frac=0.7),
                text("bbox_x1", "double precision", null_frac=0.7),
                text("bbox_y1", "double precision", null_frac=0.7),
            ],
            [
                IndexSpec("spans_pkey", 16),
                IndexSpec("spans_doc_start_end_key", 24),
                IndexSpec("idx_spans_document_page", 24, dedup_factor=0.3),
            ],
            rls_hops=0,
            reannotate_rows_per_doc=corpus.sentences_per_doc * r["ratio.spans_per_sentence"] * 0.9,
        ),
        TableSpec(
            "lexeme_occurrences",
            ann_s * r["ratio.lexocc_per_sentence"],
            [
                text("annotation_id", ulid_c, ULID_BYTES),
                text("lexeme_uid", "text", 20),
                text("span_id", uuid_c),
            ],
            [
                IndexSpec("lexeme_occurrences_pkey", ULID_BYTES + 16),
                IndexSpec("idx_occ_lexeme", 20, dedup_factor=0.4),
                IndexSpec("idx_lexeme_occurrences_span", 16),
            ],
            autovacuum_override=True,
            rls_hops=1,
            reannotate_rows_per_doc=corpus.sentences_per_doc * r["ratio.lexocc_per_sentence"],
        ),
        TableSpec(
            "dependency",
            tokens * r["ratio.dependency_per_token"],
            [
                text("annotation_id", ulid_c, ULID_BYTES),
                text("dependent_token_id", ulid_c, ULID_BYTES),
                text("head_token_id", ulid_c, ULID_BYTES, null_frac=0.04),
                text("deprel", "text", 6),
            ],
            [
                IndexSpec("dependency_pkey", 2 * ULID_BYTES),
                IndexSpec("idx_dependency_head", ULID_BYTES),
                IndexSpec("idx_dependency_dependent", ULID_BYTES),
            ],
            autovacuum_override=True,
            rls_hops=1,
            reannotate_rows_per_doc=corpus.sentences_per_doc * corpus.tokens_per_sentence,
        ),
        TableSpec(
            "token_feature",
            tokens * r["ratio.features_per_token"],
            [
                text("token_id", ulid_c, ULID_BYTES),
                text("feature", "text", 8),
                text("value", "text", 6),
            ],
            [IndexSpec("token_feature_pkey", ULID_BYTES + 8)],
            rls_hops=2,
            reannotate_rows_per_doc=(
                corpus.sentences_per_doc
                * corpus.tokens_per_sentence
                * r["ratio.features_per_token"]
            ),
        ),
        TableSpec(
            "chunk",
            ann_s * r["ratio.chunk_per_sentence"],
            [
                text("id", ulid_c, ULID_BYTES),
                text("annotation_id", ulid_c, ULID_BYTES),
                text("span_id", uuid_c),
                text("chunk_type", "text", 3),
                text("surface", "text", 18),
            ],
            [
                IndexSpec("chunk_pkey", ULID_BYTES),
                IndexSpec("idx_chunk_annotation", ULID_BYTES, dedup_factor=0.7),
                IndexSpec("idx_chunk_span", 16),
            ],
            rls_hops=1,
            reannotate_rows_per_doc=corpus.sentences_per_doc * r["ratio.chunk_per_sentence"],
        ),
        TableSpec(
            "entity_mention",
            ann_s * r["ratio.mention_per_sentence"],
            [
                text("id", ulid_c, ULID_BYTES),
                text("annotation_id", ulid_c, ULID_BYTES),
                text("span_id", uuid_c),
                text("entity_type", "text", 6),
                text("surface", "text", 15),
            ],
            [
                IndexSpec("entity_mention_pkey", ULID_BYTES),
                IndexSpec("idx_entity_mention_annotation", ULID_BYTES, dedup_factor=0.7),
                IndexSpec("idx_entity_mention_span", 16),
                IndexSpec("idx_entity_mention_type_surface", 21, dedup_factor=0.5),
            ],
            autovacuum_override=True,
            rls_hops=1,
            reannotate_rows_per_doc=corpus.sentences_per_doc * r["ratio.mention_per_sentence"],
        ),
        TableSpec(
            "sentences",
            corpus.total_sentences,
            [
                text("id", ulid_c, ULID_BYTES),
                text("document_id", uuid_c),
                text("ordinal", "integer"),
                text("text", "text", corpus.tokens_per_sentence * corpus.chars_per_token),
                text("created_at", "timestamptz"),
                text("span_id", uuid_c),
            ],
            [
                IndexSpec("sentences_pkey", ULID_BYTES),
                IndexSpec("sentences_doc_ordinal_key", 20),
                IndexSpec("idx_sentences_document", 20),  # duplicate of the unique (repo fact)
                IndexSpec("idx_sentences_span", 16),
                IndexSpec("idx_sentences_text_trgm", 0, kind="gin-trgm"),
            ],
            rls_hops=0,
        ),
        TableSpec(
            "embedding",
            ann_s * corpus.embedded_fraction,
            [
                text("span_id", uuid_c),
                text("model", "text", 24),
                text("vector", "text", 4 * 768 + 8),
            ],
            [IndexSpec("embedding_pkey", 16 + 25), IndexSpec("idx_embedding_hnsw", 0, kind="hnsw")],
            rls_hops=0,
            reannotate_rows_per_doc=corpus.sentences_per_doc * corpus.embedded_fraction,
        ),
        TableSpec(
            "document_content",
            corpus.documents,
            [
                text("document_id", uuid_c),
                text("full_text", "text", corpus.avg_doc_text_bytes),
                text("fts", "text", corpus.avg_doc_text_bytes * 0.5),
            ],
            [
                IndexSpec("document_content_pkey", 16),
                IndexSpec("idx_content_fts", 0, kind="gin-tsvector"),
            ],
            rls_hops=0,
        ),
        TableSpec(
            "document_layout_runs",
            corpus.documents
            * corpus.pdf_fraction
            * corpus.sentences_per_doc
            * r["ratio.layout_runs_per_sentence"],
            [
                text("id", uuid_c),
                text("document_id", uuid_c),
                text("ordinal", "integer"),
                text("page_number", "integer"),
                text("page_width", "double precision"),
                text("page_height", "double precision"),
                text("char_start", "integer"),
                text("char_end", "integer"),
                text("bbox_x0", "double precision"),
                text("bbox_y0", "double precision"),
                text("bbox_x1", "double precision"),
                text("bbox_y1", "double precision"),
                text("text", "text", corpus.chars_per_token * 10),
            ],
            [
                IndexSpec("document_layout_runs_pkey", 16),
                IndexSpec("layout_runs_doc_ordinal_key", 20),
                IndexSpec("idx_layout_runs_doc_chars", 24),
                IndexSpec("idx_layout_runs_doc_page", 24),
            ],
            rls_hops=0,
        ),
        TableSpec(
            "document_pages",
            corpus.documents
            * corpus.pdf_fraction
            * corpus.sentences_per_doc
            / r["ratio.sentences_per_page"],
            [
                text("id", uuid_c),
                text("document_id", uuid_c),
                text("page_number", "integer"),
                text("page_width", "double precision"),
                text("page_height", "double precision"),
                text("has_text", "boolean"),
                text("text_run_count", "integer"),
            ],
            [
                IndexSpec("document_pages_pkey", 16),
                IndexSpec("document_pages_doc_page_key", 20),
                IndexSpec("idx_document_pages_document_page", 20),
            ],
            rls_hops=0,
        ),
        TableSpec(
            "documents",
            corpus.documents,
            [
                text("id", uuid_c),
                text("tenant_id", uuid_c),
                text("title", "text", 60),
                text("description", "text", 200),
                text("language", "text", 2),
                text("source_uri", "text", 90),
                text("content_sha256", "text", 64),
                text("created_at", "timestamptz"),
                text("updated_at", "timestamptz"),
                text("lifecycle_state", "text", 8),
                text("visibility", "text", 6),
                text("owner_principal_id", uuid_c, null_frac=0.05),
                text("misc_narrow_cols", "text", 80),
            ],
            [
                IndexSpec("documents_pkey", 16),
                IndexSpec("documents_sha_key", 65),
                IndexSpec("documents_owner_principal_idx", 16, dedup_factor=0.6),
                IndexSpec("idx_documents_tenant", 16, dedup_factor=0.3),
                IndexSpec("idx_documents_created", 24),
            ],
            rls_hops=0,
        ),
        TableSpec(
            "enrichment_layers",
            ann_s * corpus.enriched_fraction * r["ratio.enrichment_rows_per_sentence"],
            [
                text("id", ulid_c, ULID_BYTES),
                text("annotation_id", ulid_c, ULID_BYTES),
                text("span_id", uuid_c),
                text("kind_payload", "text", 14),
            ],
            [
                IndexSpec("enrichment_pkey", ULID_BYTES),
                IndexSpec("idx_enrichment_annotation", ULID_BYTES, dedup_factor=0.7),
            ],
            rls_hops=1,
        ),
        TableSpec(
            "annotations",
            corpus.documents * corpus.annotated_fraction * 8,
            [
                text("id", ulid_c, ULID_BYTES),
                text("document_id", uuid_c),
                text("layer", "text", 12),
                text("tool", "text", 20),
                text("created_at", "timestamptz"),
            ],
            [
                IndexSpec("annotations_pkey", ULID_BYTES),
                IndexSpec("idx_annotations_document", 16, dedup_factor=0.7),
            ],
            rls_hops=0,
        ),
        TableSpec(
            "lexemes",
            30.0 * (corpus.total_tokens ** reg.value("ratio.lexemes_vocab_exponent")),
            [
                text("uid", "text", 20),
                text("lang", "text", 2),
                text("pos", "text", 4),
                text("lemma", "text", 9),
            ],
            [
                IndexSpec("lexemes_pkey", 20),
                IndexSpec("idx_lexemes_lemma_trgm", 0, kind="gin-trgm"),
            ],
        ),
        TableSpec(
            "jobs",
            corpus.documents * 5,
            [
                text("id", ulid_c, ULID_BYTES),
                text("kind", "text", 10),
                text("status", "text", 9),
                text("priority", "integer"),
                text("payload", "text", 120),
                text("result", "text", 150, null_frac=0.2),
                text("error", "text", 40, null_frac=0.9),
                text("attempts", "integer"),
                text("max_attempts", "integer"),
                text("document_id", uuid_c, null_frac=0.05),
                text("created_at", "timestamptz"),
                text("run_after", "timestamptz"),
                text("started_at", "timestamptz"),
                text("finished_at", "timestamptz"),
                text("heartbeat_at", "timestamptz"),
                text("locked_by", "text", 12),
                text("progress_cols", "text", 20),
            ],
            [
                IndexSpec("jobs_pkey", ULID_BYTES),
                IndexSpec("idx_jobs_document", 16 + ULID_BYTES, dedup_factor=0.8),
                IndexSpec("idx_jobs_status", 9 + ULID_BYTES, dedup_factor=0.3),
            ],
            rls_hops=0,
        ),
    ]
    reg.define(
        "corpus.jobs_per_document",
        5,
        "jobs/document",
        Source.REPO,
        "worker job kinds: ingest, annotate, semantic_embed (+reannotate/visual/metadata "
        "share) — docker-compose.yml:338-404; no jobs retention exists (migration 020 ff.)",
        "Job history is append-only and unbounded; ~5 lifetime jobs per document assumed.",
        Epistemic.EMPIRICAL,
    )
    return specs
