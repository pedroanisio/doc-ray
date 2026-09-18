"""Human-readable Markdown report.

One section per requested deliverable (sizes, throughput, connections,
latency, bottlenecks, plan effects, maintenance, margins, sensitivity,
epistemics), preceded by the corpus/assumption context that makes the numbers
auditable. Formatting only — every number comes from the model objects.
"""

from __future__ import annotations

import math

from capacity_model import __version__
from capacity_model.analysis import (
    SensitivityRow,
    capacity_margins,
    epistemic_conclusions,
    rank_bottlenecks,
)
from capacity_model.assumptions import Source
from capacity_model.model import ModelResult


def _b(n: float) -> str:
    """Human bytes."""
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} EB"


def _ms(seconds: float) -> str:
    """Human latency."""
    if not math.isfinite(seconds):
        return "∞ (saturated)"
    if seconds >= 1.0:
        return f"{seconds:,.2f} s"
    return f"{seconds * 1e3:,.1f} ms"


def _n(x: float) -> str:
    """Human count."""
    if abs(x) >= 1e9:
        return f"{x / 1e9:,.2f} B"
    if abs(x) >= 1e6:
        return f"{x / 1e6:,.1f} M"
    if abs(x) >= 1e4:
        return f"{x / 1e3:,.0f} k"
    return f"{x:,.1f}"


def _frontmatter(generated_on: str) -> str:
    """Disclaimer front matter every generated Markdown document carries."""
    return (
        "---\n"
        "disclaimer:\n"
        "  notice: >-\n"
        "    No information within this document should be taken for granted.\n"
        "    Any statement or premise not backed by a real logical definition\n"
        "    or verifiable reference may be invalid, erroneous, or a hallucination.\n"
        f'  generated_by: "scripts/capacity_model v{__version__}"\n'
        f'  date: "{generated_on}"\n'
        "---\n"
    )


def _storage_section(res: ModelResult) -> list[str]:
    """§1 — table/index sizes, calibration, growth."""
    s = res.sizes
    lines = [
        "## 1. Estimated table and index sizes at target scale",
        "",
        "| Table | Rows | Heap | TOAST | Indexes | Total |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for t in sorted(s.tables, key=lambda t: -t.total_bytes):
        lines.append(
            f"| {t.name} | {_n(t.rows)} | {_b(t.heap_bytes)} | {_b(t.toast_bytes)} "
            f"| {_b(t.total_index_bytes)} | {_b(t.total_bytes)} |"
        )
    lines += [
        f"| *~68 unmodeled small tables (remainder factor)* | — | — | — | — "
        f"| {_b(s.remainder_bytes)} |",
        f"| **Total** | | | | | **{_b(s.total_bytes)}** |",
        "",
        f"**Calibration check** — bottom-up model: {_b(s.calibration_model_bps)} per "
        f"annotated sentence vs the repo-measured ~{_b(s.calibration_measured_bps)} "
        f"(215 MB / 3,246-sentence book, docs/production-setup.md:51-77): ratio "
        f"**{s.calibration_ratio:.2f}×**. Within 2× is acceptable for a first-order "
        "storage model; the residual is dominated by GIN/tsvector density and layer "
        "coverage differences between the measured book corpus and the assumed mix.",
        "",
        "Hot working set (must stay cached for the read path):",
        "",
    ]
    for k, v in res.sizes.hot_working_set_bytes.items():
        lines.append(f"- {k.replace('_', ' ')}: {_b(v)}")
    total_ws = sum(res.sizes.hot_working_set_bytes.values())
    ram = res.hardware.ram_bytes
    lines += [
        f"- **total ≈ {_b(total_ws)}** vs {_b(ram)} RAM on `{res.hardware.name}` "
        f"({total_ws / ram:.1f}× available memory)",
        "",
    ]
    ingest = res.registry.value("behavior.expected_ingest_docs_per_day")
    per_doc = s.total_bytes / max(res.corpus.documents, 1)
    lines += [
        f"Growth while loading: ~{_b(per_doc)} per document all-in → at the expected "
        f"{ingest:,.0f} docs/day, **{_b(per_doc * ingest)}/day** "
        f"({_b(per_doc * ingest * 30)}/month) of heap+index+TOAST, plus WAL (see §7). "
        f"The documented 120 GiB provisioned volume holds ~"
        f"{120 * 1024**3 / per_doc:,.0f} documents — "
        f"{120 * 1024**3 / per_doc / res.corpus.documents * 100:.1f}% of the target corpus.",
        "",
    ]
    return lines


def _throughput_section(res: ModelResult) -> list[str]:
    """§2 — QPS/TPS per scenario."""
    lines = [
        "## 2. Queries and transactions per second by scenario",
        "",
        "| Scenario | Statements/s | Commits/s | Read stmts/s | Rows written/s | WAL |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for rep in res.scenarios:
        d = rep.dynamics
        lines.append(
            f"| {d.scenario} | {d.total_qps:,.0f} | {d.commits_s:,.1f} | "
            f"{d.read_qps:,.0f} | {_n(d.write_rows_s)} | {_b(d.wal_bytes_s)}/s |"
        )
    lines += [
        "",
        "Write rows/s are dominated by the annotation pipeline: one document "
        "annotation COPYs ~"
        f"{_n(res.operations['annotate_document'].rows_written)} layer rows in one "
        "transaction (adapters/annotation_spacy.py:458-556), and canonicalization "
        "then issues 2 autocommit statements *per entity mention* "
        "(application/canonicalize.py:43-63) — visible above as the commit-rate gap "
        "between scenarios with and without pipeline load.",
        "",
    ]
    return lines


def _connections_section(res: ModelResult) -> list[str]:
    """§3 — connections and concurrency."""
    topo = res.topology
    lines = [
        "## 3. Connection and concurrency requirements",
        "",
        f"Deployed topology (repo): API {topo.api_backends_max} pooled backends "
        f"(workers × pool max 10, config/env.registry.yaml:323), webapp "
        f"{topo.webapp_backends_max} unpooled request backends (1 conn/request, "
        f"webapp/app.py:316), workers {topo.worker_backends()} pinned "
        f"(2/process, worker.py:112-122), +{topo.overhead_connections} overhead → "
        f"worst case **{topo.total_max_backends()}** of max_connections="
        f"{res.pg.max_connections}.",
        "",
        "| Scenario | API backends needed | Webapp backends needed | API pool util | "
        "max_conn util |",
        "|---|---:|---:|---:|---:|",
    ]

    def fin(x: float, fmt: str) -> str:
        return format(x, fmt) if math.isfinite(x) else "∞ (station saturated)"

    for rep in res.scenarios:
        d = rep.dynamics
        pool = d.pool_pressure["api_pool_utilization"]
        lines.append(
            f"| {d.scenario} | {fin(d.api_backends_needed, ',.1f')} | "
            f"{fin(d.webapp_backends_needed, ',.1f')} | "
            f"{fin(pool * 100, ',.0f') if math.isfinite(pool) else '∞'}"
            f"{'%' if math.isfinite(pool) else ''} | "
            f"{d.pool_pressure['max_connections_utilization'] * 100:,.0f}% |"
        )
    lines += [
        "",
        "Structural risks independent of averages: (a) one MCP `briefing_pack` call "
        "can issue ~61 concurrent GraphQL requests → 61 pool borrows against "
        "max_size=10 per API worker — PoolTimeout (10 s) fires before "
        "statement_timeout (entrypoints/mcp/compose.py:331-411); (b) each SSE "
        "job-events subscriber holds a pooled connection for the stream's lifetime "
        "(entrypoints/api/job_events.py:88-128) — 10 subscribers exhaust one "
        "worker's pool; (c) the webapp opens a *fresh* backend per request plus one "
        "per session-store access — at peak page rates this adds connection-setup "
        "CPU on the DB host that pooled surfaces don't pay.",
        "",
    ]
    return lines


def _latency_table(res: ModelResult) -> list[str]:
    """One mean/p95 table across scenarios for one hardware profile."""
    header = (
        "| Operation | "
        + " | ".join(f"{rep.scenario.name} mean / p95" for rep in res.scenarios)
        + " |"
    )
    lines = [header, "|---|" + "---|" * len(res.scenarios)]
    op_names = [o.name for o in res.scenarios[0].dynamics.ops]
    for name in op_names:
        cells = []
        for rep in res.scenarios:
            o = next(x for x in rep.dynamics.ops if x.name == name)
            flag = " ✗" if o.breaches_statement_timeout else ""
            cells.append(f"{_ms(o.mean_response_s)} / {_ms(o.p95_response_s)}{flag}")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return lines


def _latency_section(res: ModelResult, scaled: ModelResult) -> list[str]:
    """§4 — per-operation latency on both hardware profiles."""
    lines = [
        "## 4. Estimated latency by operation type",
        "",
        "Mean / p95 modeled response (DB-side, incl. queueing and cache misses). "
        "Absolute values are first-order estimates (§10); the *ordering* and the "
        "scenario deltas are the robust signal. `✗` marks ops whose p95 exceeds the "
        "15 s API statement_timeout. '∞ (saturated)' means the scenario's load "
        "exceeds a resource's capacity on that host — the queue never drains, so "
        "no finite latency exists.",
        "",
        f"On `{res.hardware.name}` (the documented cluster):",
        "",
        *_latency_table(res),
        "",
        f"On `{scaled.hardware.name}` (candidate host sized for the target corpus):",
        "",
        *_latency_table(scaled),
        "",
    ]
    lines += [
        "",
        "The D-025 rollup strategy is why point reads stay fast: read surfaces never "
        "aggregate the fact tables live (engineering_decisions.yml:1329-1360). The "
        "slow rows above are exactly the paths that still scan or detoast at corpus "
        "scale: ILIKE sentence search (open debt, engineering_decisions.yml:1372), "
        "ts_rank tsvector detoast, KWIC full_text substring, HNSW when the graph "
        "doesn't fit in RAM.",
        "",
    ]
    return lines


def _bottleneck_section(res: ModelResult) -> list[str]:
    """§5 — bottlenecks and saturation points."""
    lines = [
        "## 5. Likely bottlenecks and saturation points",
        "",
        f"Top pressure points on `{res.hardware.name}` (utilization ≥ 1.0 = saturated):",
        "",
        "| Scenario | Resource | Utilization | Note |",
        "|---|---|---:|---|",
    ]
    for b in rank_bottlenecks(res)[:12]:
        lines.append(f"| {b.scenario} | {b.resource} | {b.utilization * 100:,.0f}% | {b.note} |")
    lines.append("")
    return lines


def _margins_section(res: ModelResult, scaled: ModelResult) -> list[str]:
    """§8 — capacity margins on both hardware profiles."""
    lines = [
        "## 8. Capacity margins by scenario",
        "",
        "| Profile | Scenario | Binding resource | Utilization | Headroom | Saturation at load × |",
        "|---|---|---|---:|---:|---:|",
    ]
    for result in (res, scaled):
        for row in capacity_margins(result):
            mult = row["saturation_multiplier"]
            mult_s = f"{mult:,.2f}" if isinstance(mult, float) and math.isfinite(mult) else "∞"
            lines.append(
                f"| {result.hardware.name} | {row['scenario']} | "
                f"{row['binding_resource']} | "
                f"{float(str(row['binding_utilization'])) * 100:,.0f}% | "
                f"{row['headroom_pct']:,.0f}% | {mult_s} |"
            )
    lines.append("")
    return lines


def _plans_section(res: ModelResult) -> list[str]:
    """§6 — cardinality/distribution effects on query plans."""
    reg = res.registry
    rls_docs = reg.value("rls.readable_docs_per_principal")
    rls_cost = rls_docs * reg.value("rls.s_per_readable_doc")
    ilike = reg.value("read.ilike_matched_sentences")
    ann_s = res.corpus.annotated_sentences
    mentions = ann_s * reg.value("ratio.mention_per_sentence")
    return [
        "## 6. Cardinality and data-distribution effects on query plans",
        "",
        f"- **RLS InitPlan scale.** Every RLS-checked query materializes "
        f"`docray_readable_documents()` once (migration 018's deliberate InitPlan "
        f"design). With org visibility (migration 110) that set is the tenant corpus: "
        f"at the assumed {rls_docs:,.0f} readable documents this adds "
        f"~{_ms(rls_cost)} CPU *per query* before any real work — linear in tenant "
        "size, invisible at today's 268 documents.",
        f"- **Skew vs default_statistics_target=100.** `entity_mention.surface` holds "
        f"~{_n(mentions)} rows with a Zipf head; 100 MCV slots cannot represent the "
        "tail, so equality selectivity for mid-frequency surfaces is misestimated — "
        "the D-017 incident (7–17 min corpus-wide entity scans) was exactly a "
        "cardinality-blind plan. Raise the column statistics target on "
        "surface/entity_type before trusting plans at 1M documents.",
        f"- **ILIKE has no usable selectivity.** `%term%` over "
        f"{_n(res.corpus.total_sentences)} sentences matches ~{_n(ilike)} rows for "
        "common terms, and the ORDER BY (title, ordinal) forces the full match set "
        "to be fetched and sorted before LIMIT 100 — the plan degrades with corpus "
        "growth regardless of the trigram index (open debt, "
        "engineering_decisions.yml:1372).",
        "- **Keyset vs OFFSET.** GraphQL paginates keyset (stable cost per page); the "
        "webapp paginates LIMIT/OFFSET (corpus_web.py:113-127) — page N scans N×page "
        "rows, so deep browsing of a 1M-row documents table degrades linearly.",
        "- **ULID vs uuid4 write locality.** ULID PKs (tokens, sentences, jobs) append "
        "rightmost in their B-trees; `gen_random_uuid()` keys (spans, documents, "
        "layout runs) scatter — each COPY'd row dirties ~"
        f"{reg.value('wal.random_index_pages_per_row'):.1f} random leaf pages, which "
        "drives the full-page-image WAL volume in §7 and the buffer churn during "
        "annotation.",
        "- **Duplicate indexes.** `sentences_document_id_ordinal_key` vs "
        "`idx_sentences_document` and the document_pages pair are verbatim duplicates "
        "(schema agent-verified) — pure write amplification on two hot tables.",
        "- **Debounced rollup recompute is a full-corpus GROUP BY.** "
        "`docray_refresh_stats_for_document` recomputes tenant-wide "
        "entity/lexeme stats at most every 10 min (migration 100:97-122) — but each "
        f"recompute scans ~{_n(mentions)} entity_mention rows; at 1M documents this "
        "becomes a minutes-long scan riding on every active-tenant job boundary.",
        "",
    ]


def _maintenance_section(res: ModelResult) -> list[str]:
    """§7 — vacuum / WAL / checkpoints."""
    lines = [
        "## 7. Vacuum, dead tuples, WAL, and checkpoint pressure",
        "",
        "| Scenario | WAL (base + FPI) | WAL/day | Checkpoint every | Forced by "
        "max_wal_size? | Autovacuum budget used |",
        "|---|---|---:|---:|---|---:|",
    ]
    for rep in res.scenarios:
        m = rep.maintenance
        lines.append(
            f"| {m.scenario} | {m.base_wal_mb_s:,.1f} + {m.fpi_wal_mb_s:,.1f} MB/s | "
            f"{m.wal_gb_per_day:,.0f} GB | {m.checkpoint_interval_s:,.0f} s | "
            f"{'yes' if m.checkpoint_forced_by_size else 'no'} | "
            f"{m.autovacuum_demand_fraction * 100:,.0f}% |"
        )
    worst = res.scenarios[-1].maintenance
    lines += [
        "",
        f"Churn detail (scenario `{worst.scenario}` — reannotation wave):",
        "",
        "| Table | Dead rows/day | Trigger at | Vacuum cycles/day | Hours/cycle |",
        "|---|---:|---:|---:|---:|",
    ]
    for t in worst.tables[:8]:
        lines.append(
            f"| {t.name} | {_n(t.dead_rows_per_day)} | {_n(t.trigger_rows)} | "
            f"{t.cycles_per_day:,.2f} | {t.vacuum_hours_per_cycle:,.1f} |"
        )
    lines += [
        "",
        "Migration 034 tightened autovacuum (scale 0.02, threshold 1000) on tokens, "
        "dependency, lexeme_occurrences, entity_mention — but *not* on spans, "
        "sentences, token_feature, chunk, or embedding, which share the same "
        "delete-then-COPY churn path. With default max_wal_size=1 GB (the repo "
        "configures no WAL GUC), pipeline scenarios force checkpoints far below the "
        "300 s timeout, and every post-checkpoint page touch re-emits a full-page "
        "image — raise max_wal_size before scaling ingest.",
        "",
        f"Replication: {worst.replication_note}",
        "",
    ]
    return lines


def _sensitivity_section(rows: list[SensitivityRow]) -> list[str]:
    """§9 — most influential assumptions."""
    lines = [
        "## 9. Assumptions with the greatest effect on the results",
        "",
        "Each opted-in assumption was halved and doubled with the whole model "
        "re-run; impact = worst |log2 change| across the headline metrics "
        "(storage, reader/FTS p95, peak CPU, saturation multiplier, WAL/day, "
        "autovacuum demand).",
        "",
        "| Assumption | Default | Impact (×½) | Impact (×2) | Most-moved metric |",
        "|---|---:|---:|---:|---|",
    ]
    for r in rows[:12]:
        lines.append(
            f"| {r.name} | {r.default:g} {r.unit} | {r.at_half:,.2f} | "
            f"{r.at_double:,.2f} | {r.worst_metric} |"
        )
    lines.append("")
    return lines


def _epistemic_section(res: ModelResult) -> list[str]:
    """§10 — analytical vs requires-empirical-validation."""
    lines = [
        "## 10. Analytical estimates vs conclusions requiring empirical validation",
        "",
        "| Conclusion | Status | Why |",
        "|---|---|---|",
    ]
    for c in epistemic_conclusions(res):
        lines.append(f"| {c['conclusion']} | {c['status']} | {c['why']} |")
    lines += [
        "",
        "Suggested validation order (cheapest decisive test first): "
        "(1) `EXPLAIN (ANALYZE, BUFFERS)` the RLS InitPlan against a synthetic "
        "100k-document tenant; (2) pgbench-style replay of the §4 read mix on a "
        "restored snapshot scaled ×10 while watching pg_stat_statements and "
        "pg_stat_io; (3) bulk-annotate a 1k-document batch and measure WAL bytes "
        "(pg_stat_wal), checkpoint cadence, and autovacuum backlog; (4) HNSW "
        "recall/latency at 10M+ vectors before committing to pgvector for 300M.",
        "",
    ]
    return lines


def _assumption_appendix(res: ModelResult) -> list[str]:
    """Appendix — the full assumption ledger."""
    lines = [
        "## Appendix A. Full assumption ledger",
        "",
        "| Name | Value | Source | Citation | Epistemic |",
        "|---|---:|---|---|---|",
    ]
    order = [Source.REPO, Source.PG_DOCS, Source.LITERATURE, Source.DERIVED, Source.ASSUMED]
    entries = sorted(
        res.registry.entries.values(),
        key=lambda a: (order.index(a.source), a.name),
    )
    for a in entries:
        lines.append(
            f"| {a.name} | {a.value:g} {a.unit} | {a.source.value} | "
            f"{a.citation} | {a.epistemic.value} |"
        )
    lines.append("")
    return lines


def build_markdown_report(
    res: ModelResult,
    scaled: ModelResult,
    sensitivity: list[SensitivityRow],
    generated_on: str,
) -> str:
    """Assemble the full human-readable report."""
    corpus = res.corpus
    head = [
        _frontmatter(generated_on),
        "# doc-ray PostgreSQL capacity model — 10,000 users / 1,000,000 documents",
        "",
        "Traceability chain: **application behavior → operation patterns → "
        "relational model and cardinality → PostgreSQL resource consumption → "
        "expected performance and failure thresholds.** Every input is a named, "
        "cited assumption (Appendix A); override any of them with "
        "`--config overrides.json`.",
        "",
        f"Corpus at target scale: {corpus.documents:,.0f} documents × "
        f"{corpus.sentences_per_doc:,.0f} sentences/doc (mixed profile) → "
        f"{_n(corpus.total_sentences)} sentences, {_n(corpus.total_tokens)} tokens, "
        f"~{_b(corpus.avg_doc_text_bytes)} text/document. Primary hardware: "
        f"`{res.hardware.description}`; comparison: `{scaled.hardware.description}`.",
        "",
    ]
    saturated = sorted(
        {
            rep.dynamics.scenario
            for rep in res.scenarios
            if rep.dynamics.cpu.saturated
            or rep.dynamics.disk_read.saturated
            or rep.dynamics.disk_write_util >= 1.0
        }
    )
    if saturated:
        head += [
            f"**Feasibility verdict:** on `{res.hardware.name}` the scenarios "
            f"{', '.join(saturated)} exceed a hardware resource outright — the "
            "corpus at target scale does not fit this host's cache or I/O budget. "
            "Sections 4–8 quantify where it breaks and what the candidate scaled "
            "host changes.",
            "",
        ]
    body = (
        _storage_section(res)
        + _throughput_section(res)
        + _connections_section(res)
        + _latency_section(res, scaled)
        + _bottleneck_section(res)
        + _plans_section(res)
        + _maintenance_section(res)
        + _margins_section(res, scaled)
        + _sensitivity_section(sensitivity)
        + _epistemic_section(res)
        + _assumption_appendix(res)
    )
    return "\n".join(head + body)
