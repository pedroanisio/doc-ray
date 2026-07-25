"""The doc-ray relational model: tables, indexes, and corpus cardinalities.

Everything here is derived from the repo (``db/migrations/*.sql``, live schema
version 1.42.0 per ``docs/DATABASE.md:3``) or from measured production numbers
the repo records; per-assumption citations are attached in
:func:`build_corpus_profile`. The catalog deliberately models the ~20 tables
that dominate capacity — the remaining ~68 bounded ops/identity/billing
tables (RBAC, OAuth, SCIM, billing, backups catalogue) are covered by an
explicit small-remainder factor rather than silently ignored.

Measured anchors used for ratios (all repo-recorded production numbers):

* Fact-table row counts at the ~143-annotated-document corpus:
  spans ~29M, tokens ~25M, lexeme_occurrences ~17.5M, chunk ~5.7M,
  entity_mention ~1.1M, sentences ~870k, embedding ~828k
  (``engineering_decisions.yml:1337-1340``, ``scripts/check_read_path_aggregates.py:52-54``).
* One 287-page book = 4,576 sentences (``db/migrations/028_...sql:6``);
  one 491k-char document = 76k tokens (``db/migrations/027_...sql:8``).
* ~215 MB of database per fully annotated + enriched document
  (``docs/production-setup.md:51-77``) — used as a *calibration anchor*,
  not as an input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from capacity_model.assumptions import AssumptionRegistry, Epistemic, Source
from capacity_model.storage import Column

#: ulid domain = char(26) (db/migrations/002_sentences.sql:8) — a 26-byte
#: short-varlena bpchar: 1-byte header + 26 bytes payload.
ULID_BYTES = 27.0


@dataclass(frozen=True)
class IndexSpec:
    """One index: average key bytes per entry (+ kind for non-btree)."""

    name: str
    key_bytes: float
    kind: str = "btree"  # btree | gin-trgm | gin-tsvector | gin-jsonb | hnsw
    dedup_factor: float = 1.0


@dataclass(frozen=True)
class TableSpec:
    """One modeled table: row shape, count, indexes, and churn semantics."""

    name: str
    row_count: float
    columns: list[Column]
    indexes: list[IndexSpec] = field(default_factory=list)
    #: migration 034 override (scale 0.02 / threshold 1000) vs stock 0.2/50.
    autovacuum_override: bool = False
    #: RLS predicate depth: 0=direct document_id, 1=via annotations, 2=via tokens.
    rls_hops: int = 0
    #: rows deleted+rewritten when one document is reannotated (per avg doc).
    reannotate_rows_per_doc: float = 0.0


@dataclass(frozen=True)
class CorpusProfile:
    """Target-scale corpus cardinalities, all derived from registered assumptions."""

    users: float
    documents: float
    sentences_per_doc: float
    tokens_per_sentence: float
    total_sentences: float
    total_tokens: float
    chars_per_token: float
    annotated_fraction: float
    enriched_fraction: float
    embedded_fraction: float
    pdf_fraction: float
    avg_doc_text_bytes: float

    @property
    def annotated_sentences(self) -> float:
        """Sentences belonging to documents that went through annotation."""
        return self.total_sentences * self.annotated_fraction


def build_corpus_profile(reg: AssumptionRegistry) -> CorpusProfile:
    """Register the corpus-shape assumptions and derive total cardinalities.

    The single most consequential assumption is the document-length mix: the
    measured corpus is book-scale (~3.2k sentences/doc, ~215 MB DB footprint
    each), which cannot describe a 1M-document corpus. The default mix below
    is mostly article-scale with a book tail — explicitly assumed, heavily
    weighted in the sensitivity pass.
    """
    users = reg.define(
        "corpus.users",
        10_000,
        "registered users",
        Source.ASSUMED,
        "task target scale",
        "Given target: ~10,000 users.",
        Epistemic.ANALYTICAL,
    )
    docs = reg.define(
        "corpus.documents",
        1_000_000,
        "documents",
        Source.ASSUMED,
        "task target scale",
        "Given target: ~1,000,000 corpus documents.",
    )
    short = reg.define(
        "corpus.mix.short_frac",
        0.85,
        "fraction",
        Source.ASSUMED,
        "conservative engineering assumption (no repo data on target mix)",
        "At 10k users × ~100 docs each, most uploads are article/report scale, "
        "not books. The measured corpus (268 docs, mostly books) cannot be "
        "extrapolated; a mixed profile is assumed instead.",
        Epistemic.EMPIRICAL,
        sensitivity_key="doc_mix",
    )
    s_short = reg.define(
        "corpus.mix.short_sentences",
        150,
        "sentences/doc",
        Source.ASSUMED,
        "conservative engineering assumption (≈10-page article at 16 sent/page)",
        "16 sentences/page measured: 4,576 sentences / 287 pages "
        "(db/migrations/028_document_delete_cascade_indexes.sql:6).",
        Epistemic.EMPIRICAL,
    )
    s_med = reg.define(
        "corpus.mix.medium_sentences",
        800,
        "sentences/doc",
        Source.ASSUMED,
        "conservative engineering assumption (~50-page report)",
        "Medium tier of the mix.",
        Epistemic.EMPIRICAL,
    )
    s_book = reg.define(
        "corpus.mix.book_sentences",
        3246,
        "sentences/doc",
        Source.REPO,
        "engineering_decisions.yml:1337-1340 (870k sentences / 268 documents)",
        "The measured corpus average — book scale.",
    )
    book_frac = reg.define(
        "corpus.mix.book_frac",
        0.02,
        "fraction",
        Source.ASSUMED,
        "conservative engineering assumption",
        "Book tail of the mix.",
        Epistemic.EMPIRICAL,
        sensitivity_key="book_frac",
    )
    # Clamp so sensitivity perturbations (×2 on a fraction) stay a valid mix.
    short_f = min(max(short.value, 0.0), 1.0)
    book_f = min(max(book_frac.value, 0.0), 1.0 - short_f)
    med_frac = max(0.0, 1.0 - short_f - book_f)
    sentences_per_doc = short_f * s_short.value + med_frac * s_med.value + book_f * s_book.value
    reg.define(
        "corpus.sentences_per_doc",
        round(sentences_per_doc, 1),
        "sentences/doc",
        Source.DERIVED,
        "mix-weighted average of the three document tiers",
        "Derived: 0.85×150 + 0.13×800 + 0.02×3246.",
        Epistemic.EMPIRICAL,
        sensitivity_key="sentences_per_doc",
    )
    tok_per_sent = reg.define(
        "corpus.tokens_per_sentence",
        25e6 / 870e3,
        "tokens/sentence",
        Source.REPO,
        "engineering_decisions.yml:1337-1340 (tokens ~25M / sentences ~870k)",
        "Measured production ratio on the live corpus.",
        sensitivity_key="tokens_per_sentence",
    )
    chars = reg.define(
        "corpus.chars_per_token",
        491_000 / 76_000,
        "chars/token",
        Source.REPO,
        "db/migrations/027_fk_cascade_indexes.sql:8 (491k chars → 76k tokens)",
        "Measured: one document's characters over its token count.",
    )
    annotated = reg.define(
        "corpus.annotated_fraction",
        1.0,
        "fraction",
        Source.ASSUMED,
        "PURPOSE.md 'Today' list: the annotation graph is a shipped, first-class product surface",
        "Target state assumes every document is annotated (current corpus is at "
        "143/268 ≈ 53% only because annotation is still catching up).",
        Epistemic.EMPIRICAL,
        sensitivity_key="annotated_fraction",
    )
    enriched = reg.define(
        "corpus.enriched_fraction",
        0.2,
        "fraction",
        Source.ASSUMED,
        "semantic_enrichment is an opt-in job (docs/nlp_capability_matrix.md:70-91)",
        "Layers 17-19 populate only for explicitly enriched documents.",
        Epistemic.EMPIRICAL,
    )
    embedded = reg.define(
        "corpus.embedded_fraction",
        1.0,
        "fraction",
        Source.REPO,
        "db/migrations/124_semantic_embedding_cutover.sql (embeddings are the standard path)",
        "Sentence embeddings are pipeline-standard after the migration-124 cutover.",
        Epistemic.EMPIRICAL,
        sensitivity_key="embedded_fraction",
    )
    pdf = reg.define(
        "corpus.pdf_fraction",
        0.6,
        "fraction",
        Source.ASSUMED,
        "conservative engineering assumption (PDF is the dominant ingest format)",
        "Only PDFs produce document_layout_runs / document_pages geometry rows.",
        Epistemic.EMPIRICAL,
    )

    def frac(x: float) -> float:
        return min(max(x, 0.0), 1.0)

    total_sentences = docs.value * sentences_per_doc
    total_tokens = total_sentences * tok_per_sent.value * frac(annotated.value)
    avg_doc_text = sentences_per_doc * tok_per_sent.value * chars.value
    return CorpusProfile(
        users=users.value,
        documents=docs.value,
        sentences_per_doc=sentences_per_doc,
        tokens_per_sentence=tok_per_sent.value,
        total_sentences=total_sentences,
        total_tokens=total_tokens,
        chars_per_token=chars.value,
        annotated_fraction=frac(annotated.value),
        enriched_fraction=frac(enriched.value),
        embedded_fraction=frac(embedded.value),
        pdf_fraction=frac(pdf.value),
        avg_doc_text_bytes=avg_doc_text,
    )


def annotation_ratios(reg: AssumptionRegistry) -> dict[str, float]:
    """Per-sentence row ratios for the annotation layers (measured or structural)."""
    cite = "engineering_decisions.yml:1337-1340 (measured fact-table rows / 870k sentences)"
    reg.define(
        "ratio.spans_per_sentence",
        29e6 / 870e3,
        "rows/sentence",
        Source.REPO,
        cite,
        "spans ~29M measured.",
        sensitivity_key="spans_ratio",
    )
    reg.define(
        "ratio.lexocc_per_sentence",
        17.5e6 / 870e3,
        "rows/sentence",
        Source.REPO,
        cite,
        "lexeme_occurrences ~17.5M measured.",
    )
    reg.define(
        "ratio.chunk_per_sentence",
        5.7e6 / 870e3,
        "rows/sentence",
        Source.REPO,
        cite,
        "chunk ~5.7M measured.",
    )
    reg.define(
        "ratio.mention_per_sentence",
        1.1e6 / 870e3,
        "rows/sentence",
        Source.REPO,
        cite,
        "entity_mention ~1.1M measured.",
    )
    reg.define(
        "ratio.dependency_per_token",
        1.0,
        "rows/token",
        Source.REPO,
        "db/migrations/009_syntax.sql:23 (PK (annotation_id, dependent_token_id))",
        "Structural: exactly one dependency row per token.",
    )
    reg.define(
        "ratio.features_per_token",
        2.0,
        "rows/token",
        Source.ASSUMED,
        "docs/nlp_annotation_layers.md:141-148 (UD FEATS examples show 0-3 per token)",
        "Midpoint of the documented 0-3 range.",
        Epistemic.EMPIRICAL,
        sensitivity_key="features_per_token",
    )
    reg.define(
        "ratio.layout_runs_per_sentence",
        2.0,
        "rows/sentence",
        Source.ASSUMED,
        "conservative engineering assumption (one row per PDF text run, ≥1 line/sentence)",
        "PDF text runs are line-grained; sentences average ~1.6 rendered lines.",
        Epistemic.EMPIRICAL,
        sensitivity_key="layout_runs",
    )
    reg.define(
        "ratio.sentences_per_page",
        4576.0 / 287.0,
        "sentences/page",
        Source.REPO,
        "db/migrations/028_document_delete_cascade_indexes.sql:6",
        "Measured book.",
    )
    reg.define(
        "ratio.enrichment_rows_per_sentence",
        3.0,
        "rows/sentence",
        Source.ASSUMED,
        "docs/nlp_annotation_layers.md:65-101 worked example "
        "(~25 layer-17/18/19 rows / 9 sentences)",
        "Discourse+timex+event+tlink+sentiment rows for enriched documents.",
        Epistemic.EMPIRICAL,
    )
    reg.define(
        "ratio.lexemes_vocab_exponent",
        0.5,
        "Heaps beta",
        Source.LITERATURE,
        "Heaps' law, V(n)=K·n^beta with beta≈0.4-0.6 for English (Heaps 1978; "
        "Manning & Schütze 1999, Foundations of Statistical NLP §5.3)",
        "Global lexeme vocabulary grows sublinearly with corpus tokens.",
        Epistemic.EMPIRICAL,
    )
    return {k: reg.value(k) for k in list(reg.entries) if k.startswith("ratio.")}
