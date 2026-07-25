"""Application behavior → operation patterns → per-operation resource demands.

Every operation below models a *real* code path found in the repo (citations
inline). Costs are decomposed into: SQL statements, DB-server CPU seconds,
logical page touches (converted to physical I/O by the cache model in
:mod:`capacity_model.dynamics`), TOAST bytes decompressed, rows/WAL written,
and commits (WAL flushes). The unit-cost coefficients are registered
assumptions — each one perturbable by the sensitivity pass.
"""

from __future__ import annotations

from dataclasses import dataclass

from capacity_model.assumptions import AssumptionRegistry, Epistemic, Source
from capacity_model.schema_catalog import CorpusProfile


@dataclass(frozen=True)
class OpCost:
    """Average resource demand of one logical operation (may span many queries)."""

    name: str
    description: str
    statements: float
    cpu_s: float
    pages: float
    #: which cached region the pages hit: hot | sentences | gin | hnsw | dim
    region: str = "hot"
    rows_written: float = 0.0
    wal_bytes: float = 0.0
    commits: float = 0.0
    conn_opens: float = 0.0
    is_write: bool = False


@dataclass(frozen=True)
class Scenario:
    """One workload scenario: user behavior knobs that drive operation rates."""

    name: str
    description: str
    active_users: float
    pageviews_per_active_min: float
    api_ops_per_active_min: float
    mcp_briefings_per_hour: float
    ingest_docs_per_day: float
    reannotate_docs_per_day: float
    cold_cache: bool = False


@dataclass
class UnitCosts:
    """Registered unit-cost coefficients shared by all operation builders."""

    cpu_tuple_s: float
    cpu_page_s: float
    stmt_overhead_s: float
    decompress_s_per_mb: float
    rls_docs: float
    rls_s_per_doc: float
    conn_setup_s: float
    wal_row_overhead: float
    index_wal_factor: float


def register_unit_costs(reg: AssumptionRegistry) -> UnitCosts:
    """Register the per-tuple / per-page / per-statement cost coefficients."""
    tup = reg.define(
        "cost.cpu_per_tuple_s",
        2e-6,
        "s/tuple",
        Source.ASSUMED,
        "conservative engineering assumption (~0.5-5 µs/tuple executor cost on "
        "modern x86; the planner's own cpu_tuple_cost=0.01 is unitless)",
        "CPU to process one tuple through the executor (filter/project/join).",
        Epistemic.EMPIRICAL,
        sensitivity_key="cpu_per_tuple",
    )
    page = reg.define(
        "cost.cpu_per_page_s",
        5e-6,
        "s/page",
        Source.ASSUMED,
        "conservative engineering assumption (buffer lookup, pin, LP scan)",
        "CPU per logical buffer access even when fully cached.",
        Epistemic.EMPIRICAL,
    )
    stmt = reg.define(
        "cost.stmt_overhead_s",
        1.5e-4,
        "s/statement",
        Source.ASSUMED,
        "conservative engineering assumption (parse+plan+protocol for simple statements)",
        "Fixed per-statement cost; doc-ray uses no prepared-statement reuse "
        "guarantees across its autocommit surfaces.",
        Epistemic.EMPIRICAL,
        sensitivity_key="stmt_overhead",
    )
    dec = reg.define(
        "cost.detoast_s_per_mb",
        1.2e-3,
        "s/MB",
        Source.ASSUMED,
        "conservative engineering assumption (pglz decompression ~0.5-1 GB/s/core)",
        "CPU to decompress TOASTed values (full_text, fts tsvector).",
        Epistemic.EMPIRICAL,
        sensitivity_key="detoast_cost",
    )
    rls_docs = reg.define(
        "rls.readable_docs_per_principal",
        20_000,
        "documents",
        Source.ASSUMED,
        "db/migrations/110_org_corpus.sql:33-55 — org-visibility branch returns every "
        "tenant document; assumed large-org tenant at target scale",
        "docray_readable_documents() materializes ALL readable doc ids per query "
        "(InitPlan); with org visibility this is the tenant's whole corpus.",
        Epistemic.EMPIRICAL,
        sensitivity_key="rls_readable_docs",
    )
    rls_cost = reg.define(
        "rls.s_per_readable_doc",
        2e-7,
        "s/doc",
        Source.ASSUMED,
        "conservative engineering assumption (hash-set insert + index-scan share per uuid)",
        "Per-uuid cost of building/probing the RLS InitPlan hash each query.",
        Epistemic.EMPIRICAL,
        sensitivity_key="rls_cost_per_doc",
    )
    conn = reg.define(
        "cost.conn_setup_s",
        3e-3,
        "s/connection",
        Source.ASSUMED,
        "conservative engineering assumption (TCP+auth+backend fork; PostgreSQL is "
        "process-per-connection, docs connect-estab)",
        "The webapp opens an UNPOOLED connection per request (webapp/app.py:316), "
        "plus one per session-store access (webapp/auth_session_postgres.py:99).",
        Epistemic.EMPIRICAL,
        sensitivity_key="conn_setup",
    )
    wal_row = reg.define(
        "wal.row_overhead_bytes",
        56,
        "bytes/row",
        Source.LITERATURE,
        "XLogRecord header 24 B + block/heap headers (src/include/access/xlogrecord.h, "
        "heapam_xlog.h); ~50-60 B typical for single-row heap records",
        "WAL framing per row-level change, on top of the row payload.",
        sensitivity_key="wal_row_overhead",
    )
    idx_wal = reg.define(
        "wal.index_amplification",
        1.8,
        "×heap WAL",
        Source.ASSUMED,
        "conservative engineering assumption (tokens carries 6 B-tree indexes — "
        "db/migrations/008/028 — each insert also WAL-logs every index entry)",
        "Extra WAL for index entries relative to heap-row WAL on the write path.",
        Epistemic.EMPIRICAL,
        sensitivity_key="index_wal_factor",
    )
    return UnitCosts(
        cpu_tuple_s=tup.value,
        cpu_page_s=page.value,
        stmt_overhead_s=stmt.value,
        decompress_s_per_mb=dec.value,
        rls_docs=rls_docs.value,
        rls_s_per_doc=rls_cost.value,
        conn_setup_s=conn.value,
        wal_row_overhead=wal_row.value,
        index_wal_factor=idx_wal.value,
    )


def _query(
    u: UnitCosts,
    tuples: float,
    pages: float,
    statements: float = 1.0,
    rls: bool = True,
    toast_mb: float = 0.0,
) -> tuple[float, float]:
    """CPU seconds and page touches for one query shape (incl. RLS InitPlan)."""
    cpu = (
        tuples * u.cpu_tuple_s
        + pages * u.cpu_page_s
        + statements * u.stmt_overhead_s
        + toast_mb * u.decompress_s_per_mb
    )
    if rls:
        cpu += u.rls_docs * u.rls_s_per_doc
        pages += 40  # documents/acl index pages feeding the InitPlan
    return cpu, pages


def _write_wal(u: UnitCosts, rows: float, row_bytes: float) -> float:
    """WAL bytes for `rows` heap inserts of `row_bytes` plus index entries."""
    heap = rows * (row_bytes + u.wal_row_overhead)
    return heap * (1.0 + u.index_wal_factor)


def build_scenarios(reg: AssumptionRegistry, corpus: CorpusProfile) -> list[Scenario]:
    """The four workload scenarios over the 10k-user population."""
    active_peak = reg.define(
        "behavior.peak_active_fraction",
        0.08,
        "fraction of users",
        Source.ASSUMED,
        "conservative engineering assumption (B2B knowledge tools: 5-10% peak concurrency)",
        "Fraction of the 10k registered users concurrently active at daily peak.",
        Epistemic.EMPIRICAL,
        sensitivity_key="peak_active_fraction",
    ).value
    pv = reg.define(
        "behavior.pageviews_per_active_min",
        2.0,
        "pages/min",
        Source.ASSUMED,
        "conservative engineering assumption (reading-heavy UI, one action ~every 30 s)",
        "Webapp page loads per concurrently active user per minute.",
        Epistemic.EMPIRICAL,
        sensitivity_key="pageviews_per_min",
    ).value
    api = reg.define(
        "behavior.api_ops_per_active_min",
        1.5,
        "ops/min",
        Source.ASSUMED,
        "conservative engineering assumption (GraphQL/agent traffic alongside browsing)",
        "GraphQL operations (incl. MCP-forwarded reads) per active user per minute.",
        Epistemic.EMPIRICAL,
    ).value
    ingest = reg.define(
        "behavior.expected_ingest_docs_per_day",
        2200,
        "docs/day",
        Source.DERIVED,
        "1,000,000 documents ÷ ~15 months of sustained loading",
        "Steady ingest needed to reach the target corpus in a realistic window.",
        Epistemic.EMPIRICAL,
        sensitivity_key="ingest_rate",
    ).value
    reann = reg.define(
        "behavior.reannotate_docs_per_day_wave",
        20_000,
        "docs/day",
        Source.ASSUMED,
        "conservative engineering assumption (model-upgrade wave: 2% of corpus/day)",
        "Reannotation wave rate in the degraded scenario (delete-then-COPY churn).",
        Epistemic.EMPIRICAL,
    ).value
    users = corpus.users
    return [
        Scenario(
            "baseline",
            "Quiet period: off-hours traffic, trickle ingest.",
            active_users=users * 0.01,
            pageviews_per_active_min=pv,
            api_ops_per_active_min=api,
            mcp_briefings_per_hour=2,
            ingest_docs_per_day=ingest * 0.2,
            reannotate_docs_per_day=0,
        ),
        Scenario(
            "expected",
            "Business hours: typical concurrent load + steady ingest.",
            active_users=users * 0.03,
            pageviews_per_active_min=pv,
            api_ops_per_active_min=api,
            mcp_briefings_per_hour=12,
            ingest_docs_per_day=ingest,
            reannotate_docs_per_day=ingest * 0.1,
        ),
        Scenario(
            "peak",
            "Daily peak + bulk-import burst (5× steady ingest).",
            active_users=users * active_peak,
            pageviews_per_active_min=pv * 1.5,
            api_ops_per_active_min=api * 2,
            mcp_briefings_per_hour=40,
            ingest_docs_per_day=ingest * 5,
            reannotate_docs_per_day=ingest * 0.5,
        ),
        Scenario(
            "degraded",
            "Peak traffic during a reannotation wave with a cold cache "
            "(post-failover/restart): worst credible day.",
            active_users=users * active_peak,
            pageviews_per_active_min=pv * 1.5,
            api_ops_per_active_min=api * 2,
            mcp_briefings_per_hour=40,
            ingest_docs_per_day=ingest * 5,
            reannotate_docs_per_day=reann,
            cold_cache=True,
        ),
    ]


def op_rates(sc: Scenario, ops: dict[str, OpCost]) -> dict[str, float]:
    """Operations/second for one scenario (the page/API mix is fixed here).

    Mix weights follow the read surfaces the webapp manual and MCP expose
    (docs/webapp_user_manual.md; entrypoints/mcp) — reader-dominated browsing
    with a search tail. Weights are code constants, not hidden: they are
    reported per-op in the output.
    """
    pages_s = sc.active_users * sc.pageviews_per_active_min / 60.0
    api_s = sc.active_users * sc.api_ops_per_active_min / 60.0
    ingest_s = sc.ingest_docs_per_day / 86_400.0
    reann_s = sc.reannotate_docs_per_day / 86_400.0
    pipeline_s = ingest_s + reann_s  # every (re)ingested doc annotates + embeds
    rates = {
        "webapp_reader_page": pages_s * 0.55,
        "webapp_corpus_list": pages_s * 0.30,
        "api_document_list": api_s * 0.30,
        "api_read_sentences": api_s * 0.25,
        "api_full_text_search": api_s * 0.12,
        "api_sentence_search_ilike": pages_s * 0.10 + api_s * 0.10,
        "api_semantic_search": api_s * 0.08,
        "api_lexeme_kwic": api_s * 0.05 + pages_s * 0.05,
        "api_entity_frequency": api_s * 0.10,
        "mcp_briefing_pack": sc.mcp_briefings_per_hour / 3600.0,
        "ingest_document": ingest_s,
        "annotate_document": pipeline_s,
        "embed_document": pipeline_s,
        "canonicalize_document": pipeline_s,
        "stats_refresh": pipeline_s * 3,
        "metadata_edit": sc.active_users * 0.2 / 60.0,
        "worker_queue_tick": 10 / 5.0,
    }
    return {k: v for k, v in rates.items() if k in ops}
