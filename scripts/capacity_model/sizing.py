"""Storage projections: heap, TOAST, B-tree/GIN/HNSW indexes, cache working set.

Consumes the table catalog (:mod:`capacity_model.schema_catalog`) and the
storage-format estimators (:mod:`capacity_model.storage`); registers the fuzzy
coefficients (GIN posting bytes, tsvector density, trigram density) as
explicit, sensitivity-enabled assumptions.

A *calibration check* compares the bottom-up per-annotated-sentence footprint
against the repo's own measured anchor — ~215 MB of database per fully
annotated + enriched book of ~3,246 sentences ≈ 66 KB/sentence
(``docs/production-setup.md:51-77``) — and reports the ratio, so the reader
can judge how much to trust the absolute sizes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from capacity_model.assumptions import AssumptionRegistry, Epistemic, Source
from capacity_model.schema_catalog import CorpusProfile, TableSpec
from capacity_model.storage import (
    btree_index_bytes,
    gin_index_bytes,
    heap_table_bytes,
    hnsw_index_bytes,
)

#: Repo-measured anchor: 215 MB DB per fully annotated document of ~3,246
#: sentences (docs/production-setup.md:51-77, engineering_decisions.yml:1337-1340).
MEASURED_BYTES_PER_ANNOTATED_SENTENCE = 215e6 / 3246.0


@dataclass(frozen=True)
class TableSize:
    """Size breakdown for one table."""

    name: str
    rows: float
    heap_bytes: float
    toast_bytes: float
    index_bytes: dict[str, float]
    btree_heights: dict[str, int]

    @property
    def total_index_bytes(self) -> float:
        """Sum of all index bytes on this table."""
        return sum(self.index_bytes.values())

    @property
    def total_bytes(self) -> float:
        """Heap + TOAST + indexes."""
        return self.heap_bytes + self.toast_bytes + self.total_index_bytes


@dataclass(frozen=True)
class SizingResult:
    """Corpus-wide storage projection."""

    tables: list[TableSize]
    remainder_bytes: float
    calibration_model_bps: float
    calibration_measured_bps: float
    hot_working_set_bytes: dict[str, float] = field(default_factory=dict)

    @property
    def total_bytes(self) -> float:
        """Grand total including the small-table remainder factor."""
        return sum(t.total_bytes for t in self.tables) + self.remainder_bytes

    @property
    def calibration_ratio(self) -> float:
        """Bottom-up ÷ measured per-annotated-sentence bytes (1.0 = perfect)."""
        return self.calibration_model_bps / self.calibration_measured_bps

    def table(self, name: str) -> TableSize:
        """Lookup one table's size by name."""
        return next(t for t in self.tables if t.name == name)


def _gin_coefficients(reg: AssumptionRegistry) -> dict[str, float]:
    """Register the GIN/tsvector/trigram density coefficients (all empirical)."""
    reg.define(
        "gin.bytes_per_posting",
        2.5,
        "bytes",
        Source.ASSUMED,
        "PostgreSQL docs gin.html (varbyte-compressed posting lists since 9.4); "
        "2-6 bytes/pointer typical, midpoint-low taken",
        "Compressed item-pointer cost per (key, row) posting.",
        Epistemic.EMPIRICAL,
        sensitivity_key="gin_posting_bytes",
    )
    reg.define(
        "gin.page_utilization",
        0.75,
        "fraction",
        Source.ASSUMED,
        "conservative engineering assumption (GIN pages are not fillfactor-packed)",
        "Fraction of GIN page bytes holding payload.",
        Epistemic.EMPIRICAL,
    )
    reg.define(
        "fts.distinct_terms_per_doc",
        1200,
        "lexemes/doc",
        Source.ASSUMED,
        "conservative engineering assumption (type-token ratio ~0.15-0.25 at "
        "~8.6k tokens/doc; Heaps' law, Manning & Schütze 1999 §5.3)",
        "Distinct indexed terms per average document → tsvector GIN postings.",
        Epistemic.EMPIRICAL,
        sensitivity_key="fts_terms_per_doc",
    )
    reg.define(
        "trgm.distinct_trigrams_per_sentence",
        150,
        "trigrams/sentence",
        Source.ASSUMED,
        "pg_trgm docs (trigram extraction over ~186-char sentences; near one "
        "trigram per character minus repeats)",
        "Distinct trigrams per sentence → postings in idx_sentences_text_trgm.",
        Epistemic.EMPIRICAL,
        sensitivity_key="trgm_per_sentence",
    )
    reg.define(
        "hnsw.m_links",
        16,
        "links/node",
        Source.REPO,
        "db/migrations/012_embeddings.sql:14-15 (CREATE INDEX ... USING hnsw with "
        "no parameters → pgvector defaults m=16, ef_construction=64)",
        "HNSW graph degree; default because the repo never overrides it.",
    )
    return {
        "posting": reg.value("gin.bytes_per_posting"),
        "util": reg.value("gin.page_utilization"),
        "fts_terms": reg.value("fts.distinct_terms_per_doc"),
        "trgm": reg.value("trgm.distinct_trigrams_per_sentence"),
        "m": reg.value("hnsw.m_links"),
    }


def _index_bytes(
    spec: TableSpec, coeff: dict[str, float], corpus: CorpusProfile
) -> tuple[dict[str, float], dict[str, int]]:
    """Estimate every index on one table; returns (bytes by index, btree heights)."""
    sizes: dict[str, float] = {}
    heights: dict[str, int] = {}
    for idx in spec.indexes:
        if idx.kind == "btree":
            est = btree_index_bytes(spec.row_count, idx.key_bytes, idx.dedup_factor)
            sizes[idx.name] = est.total_bytes
            heights[idx.name] = est.height
        elif idx.kind == "gin-tsvector":
            postings = corpus.documents * coeff["fts_terms"]
            sizes[idx.name] = gin_index_bytes(
                postings,
                distinct_keys=600_000,
                avg_key_bytes=9,
                bytes_per_posting=coeff["posting"],
                page_utilization=coeff["util"],
            )
        elif idx.kind == "gin-trgm":
            postings = spec.row_count * coeff["trgm"]
            sizes[idx.name] = gin_index_bytes(
                postings,
                distinct_keys=30_000,
                avg_key_bytes=4,
                bytes_per_posting=coeff["posting"],
                page_utilization=coeff["util"],
            )
        elif idx.kind == "hnsw":
            sizes[idx.name] = hnsw_index_bytes(spec.row_count, 768, int(coeff["m"]))
        else:  # gin-jsonb and anything unforeseen: treat like tsvector-lite
            sizes[idx.name] = gin_index_bytes(
                spec.row_count * 8,
                distinct_keys=10_000,
                avg_key_bytes=12,
                bytes_per_posting=coeff["posting"],
                page_utilization=coeff["util"],
            )
    return sizes, heights


def _hot_working_set(
    reg: AssumptionRegistry, tables: list[TableSize], corpus: CorpusProfile
) -> dict[str, float]:
    """Bytes that must stay cached for the read path to avoid random I/O.

    Composition: all B-tree upper levels (root/internal pages), the rollup
    tables, the documents/collections dimension tables, plus the hot-document
    fraction of sentence/content data, plus the HNSW index (graph traversal
    touches memory-random pages; pgvector guidance is that HNSW should be
    RAM-resident for its latency profile).
    """
    hot_frac = reg.define(
        "cache.hot_doc_fraction",
        0.05,
        "fraction of documents",
        Source.ASSUMED,
        "conservative engineering assumption (80/20-style access skew)",
        "The fraction of documents receiving ~80% of reads in a given window.",
        Epistemic.EMPIRICAL,
        sensitivity_key="hot_doc_fraction",
    ).value
    by_name = {t.name: t for t in tables}
    upper_levels = sum(
        t.total_index_bytes * 0.01
        for t in tables  # ≈ internal/leaf ratio at fanout ~300
    )
    hot_docs = 0.0
    for name in ("sentences", "document_content", "tokens", "entity_mention", "spans"):
        if name in by_name:
            t = by_name[name]
            hot_docs += (t.heap_bytes + t.toast_bytes + t.total_index_bytes * 0.2) * hot_frac
    dimensions = sum(
        by_name[n].total_bytes for n in ("documents", "annotations", "lexemes") if n in by_name
    )
    hnsw = sum(b for t in tables for n, b in t.index_bytes.items() if "hnsw" in n)
    return {
        "btree_upper_levels": upper_levels,
        "hot_document_slice": hot_docs,
        "dimension_tables": dimensions,
        "hnsw_index_resident": hnsw,
    }


def size_corpus(
    reg: AssumptionRegistry, corpus: CorpusProfile, tables: list[TableSpec]
) -> SizingResult:
    """Project all modeled tables at target scale and run the calibration check."""
    coeff = _gin_coefficients(reg)
    remainder_frac = reg.define(
        "sizing.remainder_fraction",
        0.03,
        "fraction",
        Source.ASSUMED,
        "conservative engineering assumption",
        "The ~68 bounded ops/identity/billing tables not modeled row-by-row "
        "(RBAC, OAuth, SCIM, audit, billing) as a fraction of modeled bytes.",
        Epistemic.EMPIRICAL,
    ).value
    sized: list[TableSize] = []
    for spec in tables:
        heap, toast = heap_table_bytes(spec.row_count, spec.columns)
        idx_bytes, heights = _index_bytes(spec, coeff, corpus)
        sized.append(
            TableSize(
                name=spec.name,
                rows=spec.row_count,
                heap_bytes=heap,
                toast_bytes=toast,
                index_bytes=idx_bytes,
                btree_heights=heights,
            )
        )
    modeled = sum(t.total_bytes for t in sized)
    # Calibration: per-annotated-sentence bytes across annotation-driven tables.
    ann_tables = {
        "tokens",
        "spans",
        "lexeme_occurrences",
        "dependency",
        "token_feature",
        "chunk",
        "sentences",
        "embedding",
        "entity_mention",
        "enrichment_layers",
        "document_content",
    }
    ann_bytes = sum(t.total_bytes for t in sized if t.name in ann_tables)
    ann_sentences = max(corpus.annotated_sentences, 1.0)
    model_bps = ann_bytes / ann_sentences
    result = SizingResult(
        tables=sized,
        remainder_bytes=modeled * remainder_frac,
        calibration_model_bps=model_bps,
        calibration_measured_bps=MEASURED_BYTES_PER_ANNOTATED_SENTENCE,
    )
    return SizingResult(
        tables=result.tables,
        remainder_bytes=result.remainder_bytes,
        calibration_model_bps=result.calibration_model_bps,
        calibration_measured_bps=result.calibration_measured_bps,
        hot_working_set_bytes=_hot_working_set(reg, sized, corpus),
    )
