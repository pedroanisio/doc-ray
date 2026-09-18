"""The operation catalog: per-operation resource demands from real code paths.

Split out of :mod:`capacity_model.workload` for the D-015 per-file size gate.
Each operation models an actual query/write path found in the repo (citations
inline); the shared unit-cost coefficients and the ``_query``/``_write_wal``
composition helpers live in the workload module.
"""

from __future__ import annotations

from capacity_model.assumptions import AssumptionRegistry, Epistemic, Source
from capacity_model.schema_catalog import CorpusProfile
from capacity_model.sizing import SizingResult
from capacity_model.workload import OpCost, UnitCosts, _query, _write_wal


def build_operations(
    reg: AssumptionRegistry, corpus: CorpusProfile, sizes: SizingResult, u: UnitCosts
) -> dict[str, OpCost]:
    """Instantiate the operation catalog from the repo's actual code paths."""
    sent_per_doc = corpus.sentences_per_doc
    tok_per_doc = sent_per_doc * corpus.tokens_per_sentence
    doc_text_mb = corpus.avg_doc_text_bytes / 1e6
    ilike_matches = reg.define(
        "read.ilike_matched_sentences",
        20_000,
        "rows",
        Source.ASSUMED,
        "conservative engineering assumption (common-term selectivity ~7e-5 of "
        f"{corpus.total_sentences:.0f} sentences)",
        "searchSentences runs `text ILIKE '%term%'` over the whole corpus with "
        "ORDER BY title (persistence_read.py:640-650) — matches before LIMIT 100.",
        Epistemic.EMPIRICAL,
        sensitivity_key="ilike_matches",
    ).value
    fts_candidates = reg.define(
        "read.fts_candidate_docs",
        500,
        "rows",
        Source.ASSUMED,
        "conservative engineering assumption (document-frequency of a typical query)",
        "fullTextSearch candidates that must be rank-fetched; ts_rank detoasts each "
        "candidate's tsvector (persistence_read.py:802-824; textsearch-controls docs).",
        Epistemic.EMPIRICAL,
        sensitivity_key="fts_candidates",
    ).value
    hnsw_probes = reg.define(
        "read.hnsw_page_probes",
        250,
        "pages",
        Source.LITERATURE,
        "HNSW search visits ~ef_search·M/2 nodes (Malkov & Yashunin 2018); pgvector "
        "default ef_search=40, m=16; one ~3.1 KB vector per page probe",
        "Random index-page touches per semantic search over the HNSW graph.",
        Epistemic.EMPIRICAL,
        sensitivity_key="hnsw_probes",
    ).value

    ops: dict[str, OpCost] = {}

    def add(op: OpCost) -> None:
        ops[op.name] = op

    # --- READ PATH ------------------------------------------------------------
    cpu, pages = _query(u, tuples=40 * 3 + 500, pages=90, statements=6)
    add(
        OpCost(
            "webapp_reader_page",
            "Webapp reader: 40-sentence OFFSET page + count + pages list + doc row "
            "(webapp/document_web.py:229-311); 2 unpooled connections per request.",
            statements=6,
            cpu_s=cpu + 2 * u.conn_setup_s,
            pages=pages,
            region="hot",
            conn_opens=2,
        )
    )
    cpu, pages = _query(u, tuples=25 * 12 + 250, pages=120, statements=4)
    add(
        OpCost(
            "webapp_corpus_list",
            "Webapp corpus list: OFFSET page of documents + per-row string_agg over "
            "annotations + rollup joins (webapp/corpus_web.py:113-127).",
            statements=4,
            cpu_s=cpu + 2 * u.conn_setup_s,
            pages=pages,
            region="dim",
            conn_opens=2,
        )
    )
    cpu, pages = _query(u, tuples=50 * 10 + 50 * 4, pages=260, statements=10)
    add(
        OpCost(
            "api_document_list",
            "GraphQL documents(limit:50): keyset page + 5 DataLoader batches + live "
            "n_provenance count per row (persistence_read.py:130-198,166-169).",
            statements=10,
            cpu_s=cpu,
            pages=pages,
            region="dim",
        )
    )
    cpu, pages = _query(u, tuples=200 * 2, pages=30 + 200 // 20, statements=1)
    add(
        OpCost(
            "api_read_sentences",
            "Ranged sentence read, keyset (ordinal,id), limit 200, join spans "
            "(persistence_read.py:226-278; MCP default limit=200).",
            statements=1,
            cpu_s=cpu,
            pages=pages,
            region="hot",
        )
    )
    fts_toast_mb = fts_candidates * (corpus.avg_doc_text_bytes * 0.5 * 0.5) / 1e6
    cpu, pages = _query(
        u,
        tuples=fts_candidates * 3,
        pages=60 + fts_candidates * 2,
        statements=1,
        toast_mb=fts_toast_mb,
    )
    add(
        OpCost(
            "api_full_text_search",
            "fullTextSearch: GIN fts probe, then ts_rank detoasts each candidate's "
            "tsvector; lateral per-row ts_config defeats a single-tsquery probe "
            "(persistence_read.py:802-824).",
            statements=1,
            cpu_s=cpu,
            pages=pages,
            region="gin",
        )
    )
    cpu, pages = _query(
        u,
        tuples=ilike_matches * 1.3,
        pages=ilike_matches * 0.35 + 200,
        statements=1,
    )
    add(
        OpCost(
            "api_sentence_search_ilike",
            "searchSentences: leading-wildcard ILIKE over all sentences, ORDER BY "
            "(title,ordinal) sorts the full match set before LIMIT 100 "
            "(persistence_read.py:640-650; open debt engineering_decisions.yml:1372).",
            statements=1,
            cpu_s=cpu,
            pages=pages,
            region="sentences",
        )
    )
    cpu, pages = _query(u, tuples=400, pages=hnsw_probes + 40, statements=2)
    add(
        OpCost(
            "api_semantic_search",
            "Embedding similarity search over HNSW (SET LOCAL hnsw.iterative_scan + "
            "probe; persistence_semantic_read.py); random pages across the graph.",
            statements=2,
            cpu_s=cpu,
            pages=pages,
            region="hnsw",
        )
    )
    kwic_docs = 20.0
    cpu, pages = _query(
        u,
        tuples=80 * 6,
        pages=80 * 3 + 60,
        statements=1,
        toast_mb=kwic_docs * doc_text_mb * 0.5,
    )
    add(
        OpCost(
            "api_lexeme_kwic",
            "KWIC: 5-table join, substring(full_text) detoast per row across ~20 "
            "distinct documents (persistence_read.py:542-610).",
            statements=1,
            cpu_s=cpu,
            pages=pages,
            region="hot",
        )
    )
    cpu, pages = _query(u, tuples=60, pages=12, statements=1)
    add(
        OpCost(
            "api_entity_frequency",
            "entityFrequency corpus-wide: rollup entity_frequency_stats read, keyset "
            "limit 50 (persistence_read.py:730-799, migration 100).",
            statements=1,
            cpu_s=cpu,
            pages=pages,
            region="dim",
        )
    )
    fts, ilike_lite, ef = (
        ops["api_full_text_search"],
        ops["api_sentence_search_ilike"],
        ops["api_entity_frequency"],
    )
    add(
        OpCost(
            "mcp_briefing_pack",
            "MCP briefing_pack: up to 61 GraphQL requests — 7 FTS-class + 48 term "
            "searches + 6 entity_frequency (entrypoints/mcp/compose.py:331-411).",
            statements=61,
            cpu_s=7 * fts.cpu_s + 48 * ilike_lite.cpu_s * 0.3 + 6 * ef.cpu_s,
            pages=7 * fts.pages + 48 * ilike_lite.pages * 0.3 + 6 * ef.pages,
            region="sentences",
        )
    )

    # --- WRITE PATH -----------------------------------------------------------
    ingest_rows = sent_per_doc * (1 + 1 + corpus.pdf_fraction * 2) + 30
    wal = _write_wal(u, ingest_rows, 120) + corpus.avg_doc_text_bytes * 1.6
    cpu, pages = _query(
        u,
        tuples=ingest_rows * 2,
        pages=ingest_rows / 10 + 80,
        statements=20,
        rls=False,
        toast_mb=doc_text_mb,
    )
    add(
        OpCost(
            "ingest_document",
            "insert_document: one transaction — documents + content(TOAST+fts trigger) "
            "+ spans COPY + sentences executemany + layout runs + provenance "
            "(adapters/persistence.py:277-465).",
            statements=20,
            cpu_s=cpu,
            pages=pages,
            region="hot",
            is_write=True,
            rows_written=ingest_rows,
            wal_bytes=wal,
            commits=2,
        )
    )
    ann_rows = (
        tok_per_doc * (1 + reg.value("ratio.features_per_token") + 1)  # tokens+feat+dep
        + sent_per_doc
        * (
            reg.value("ratio.spans_per_sentence") * 0.9
            + reg.value("ratio.chunk_per_sentence")
            + reg.value("ratio.mention_per_sentence")
            + reg.value("ratio.lexocc_per_sentence")
        )
    )
    wal = _write_wal(u, ann_rows, 110)
    cpu, pages = _query(
        u, tuples=ann_rows * 1.5, pages=ann_rows / 12 + 200, statements=30, rls=False
    )
    add(
        OpCost(
            "annotate_document",
            "spaCy annotate: one transaction per document — DELETE old run (cascade) + "
            "5 layer COPYs + coref loop + lexeme_occurrences COPY "
            "(adapters/annotation_spacy.py:458-556).",
            statements=30,
            cpu_s=cpu,
            pages=pages,
            region="hot",
            is_write=True,
            rows_written=ann_rows,
            wal_bytes=wal,
            commits=2,
        )
    )
    wal_per_vec = reg.define(
        "wal.hnsw_bytes_per_insert",
        20_000,
        "bytes/vector",
        Source.ASSUMED,
        "conservative engineering assumption (HNSW insert dirties multiple graph "
        "pages; a 2.7× DB-CPU penalty was measured for row-at-a-time inserts on "
        "this index in one deployment)",
        "WAL per embedding row including HNSW graph-page changes.",
        Epistemic.EMPIRICAL,
        sensitivity_key="hnsw_wal_per_insert",
    ).value
    n_vec = sent_per_doc * corpus.embedded_fraction
    cpu, pages = _query(u, tuples=n_vec * 4, pages=n_vec * 6, statements=4, rls=False)
    add(
        OpCost(
            "embed_document",
            "semantic_embed: delete-then-COPY sentence vectors; every row pays an HNSW "
            "graph insert (application/semantic_embed.py:56-96, migration 012).",
            statements=4,
            cpu_s=cpu + n_vec * 1.5e-3,
            pages=pages,
            region="hnsw",
            is_write=True,
            rows_written=n_vec,
            wal_bytes=n_vec * (3120 + wal_per_vec),
            commits=2,
        )
    )
    mentions = sent_per_doc * reg.value("ratio.mention_per_sentence")
    cpu, pages = _query(
        u, tuples=mentions * 6, pages=mentions * 2, statements=mentions * 2, rls=False
    )
    add(
        OpCost(
            "canonicalize_document",
            "canonicalize.autolink: 2 statements PER MENTION on an autocommit "
            "connection — one commit per link (application/canonicalize.py:43-63).",
            statements=mentions * 2,
            cpu_s=cpu,
            pages=pages,
            region="dim",
            is_write=True,
            rows_written=mentions,
            wal_bytes=_write_wal(u, mentions, 90),
            commits=mentions,
        )
    )
    rollup_rows = reg.value("ratio.mention_per_sentence") * corpus.annotated_sentences
    cpu, pages = _query(
        u, tuples=rollup_rows * 0.001, pages=rollup_rows * 0.0002, statements=4, rls=False
    )
    add(
        OpCost(
            "stats_refresh",
            "docray_refresh_stats_for_document per job + debounced (10 min) tenant "
            "rollup recompute = full GROUP BY over entity_mention/lexeme_occurrences "
            "scaled to per-job amortized share (migration 100:97-122).",
            statements=4,
            cpu_s=cpu,
            pages=pages,
            region="sentences",
            is_write=True,
            rows_written=200,
            wal_bytes=_write_wal(u, 200, 60),
            commits=2,
        )
    )
    cpu, pages = _query(u, tuples=30, pages=15, statements=3)
    add(
        OpCost(
            "metadata_edit",
            "User metadata edit: documents UPDATE + document_metadata_history append "
            "(migration 045) + stats touch.",
            statements=3,
            cpu_s=cpu,
            pages=pages,
            region="dim",
            is_write=True,
            rows_written=3,
            wal_bytes=_write_wal(u, 3, 300),
            commits=1,
        )
    )
    cpu, pages = _query(u, tuples=12, pages=8, statements=5, rls=False)
    add(
        OpCost(
            "worker_queue_tick",
            "Idle worker loop per 5s: claim probe (partial index) + reap_stale's 3 "
            "UPDATEs + amortized janitor/heartbeat (queue_postgres.py:44-69,445-476).",
            statements=5,
            cpu_s=cpu,
            pages=pages,
            region="dim",
            is_write=True,
            rows_written=0.5,
            wal_bytes=400,
            commits=4,
        )
    )
    return ops
