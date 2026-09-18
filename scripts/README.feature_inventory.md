---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-24"
doc_type: readme
---

# gen_feature_inventory.py

Mechanically extract a quantitative inventory of every capability this codebase
offers — with counts, categories, source locations, and a confidence band derived
from evidence rather than asserted.

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

```bash
python scripts/gen_feature_inventory.py                    # markdown report to stdout
python scripts/gen_feature_inventory.py --format json      # full records + evidence
python scripts/gen_feature_inventory.py --out docs/FEATURES.md
python scripts/gen_feature_inventory.py --summary          # counts only, no detail tables
```

---

## Why it exists

A hand-maintained feature list is a claim; it drifts the moment someone adds a
tool and forgets the doc. This tool makes no claims of its own. It runs the
production wiring and reports what that wiring contains — so the inventory is
wrong only if the system is wrong.

Concretely: it answers "what does doc-ray expose, how much of it, where does each
piece live, and how strong is the evidence that it is really there?"

---

## What it reads

Nine collectors, seven of which introspect **live** registries — they import and
execute the same code paths the deployed system uses. Nothing can appear in the
report that is not registered, and nothing registered can be omitted.

| Collector | Category emitted | Evidence method |
|---|---|---|
| `graphql` | `graphql.query`, `graphql.mutation`, `graphql.subscription` | Builds the real Strawberry schema; walks field definitions and resolvers |
| `mcp` | `mcp.tool.read`, `mcp.tool.write`, `mcp.resource` | Imports the `TOOLS` / `RESOURCES` tuples the MCP server registers |
| `http` | `http.route` | Walks the FastAPI route table, descending into mounted routers |
| `webapp` | `webapp.route` | Walks the Flask `url_map` and `view_functions` |
| `jobs` | `job.kind` | `KIND_*` constants cross-checked against the worker's `HANDLERS` dict |
| `formats` | `ingest.format` | The extractor dispatch table + its content-type map |
| `annotation` | `annotation.layer`, `annotation.language` | The spaCy capability registry, including installed-model state |
| `config` | `config.variable` | *Static*: `config/env.registry.yaml` |
| `scripts` | `ops.script` | *Static*: AST parse of `scripts/*.py` + module docstrings |

The last two have no runtime registry to interrogate, so their records are marked
`static-declaration` and are **capped below the top confidence band** (see below).
That asymmetry is deliberate and visible in the output rather than hidden in it.

---

## Evidence model

Each record accumulates signals. The score is the sum of their weights; the
confidence band is a pure function of that score.

| Signal | Weight | Meaning |
|---|---:|---|
| `runtime-registry` | 2 | Enumerated by *executing* the production wiring |
| `static-declaration` | 1 | Parsed from a checked-in declarative source of truth |
| `source-located` | 1 | Resolved to a concrete file and line |
| `documented` | 1 | Docstring or registry description present |
| `tested` | 1 | Identifier referenced by ≥1 file in the test suites |
| `handler-bound` | 1 | Declared identifier wired to an executable handler |

Bands: **high** ≥ 4, **medium** ≥ 2, **low** ≥ 0.

**The runtime cap.** A record without `runtime-registry` never reports `high`,
regardless of how many weaker signals it collects. A declaration proves intent;
execution proves existence. Without the cap, a well-described, well-tested YAML
entry would rank alongside a tool the server demonstrably registers, and the
band ordering would quietly stop meaning anything.

### What `tested` does and does not prove

The corroboration index is lexical: it loads every file under `tests/` and
`src/doc_ray/tests/` and asks whether any of them mentions the capability's
identifier, on word boundaries, with a minimum probe length (`MIN_PROBE_LEN`).
Ambiguous identifiers are quoted or parenthesised by the collector — the job kind
`delete` is probed as `KIND_DELETE` and `"delete"`, never as the bare word — so a
common English term cannot corroborate a capability by accident.

It answers *"is this name exercised anywhere?"*. It does **not** answer *"is this
behaviour covered?"*. A 100 % `tested` column means every identifier in that
category appears in the suite, not that every code path is verified. Treat it as
one weak signal among six, which is exactly the weight it carries.

---

## Output

### Markdown (default)

Provenance header (package version, commit, latest migration, extraction
timestamp, test files indexed) → extraction-integrity table (per collector:
method, status, count) → evidence model → counts by category → per-category
detail tables with `file:line`, score, band, and test-reference count.

### JSON (`--format json`)

```json
{
  "meta": { "generated_at": "…", "package_version": "…", "git_commit": "…",
            "signal_weights": {…}, "confidence_bands": {…} },
  "collectors": [ { "name": "mcp", "method": "runtime:tool-registry",
                    "ok": true, "error": "", "features_detected": 48 } ],
  "summary": { "mcp.tool.read": { "total": 22, "high": 22, "medium": 0,
                                  "low": 0, "tested": 22 } },
  "totals": { "features": 426, "high": 236, "medium": 190, "low": 0 },
  "features": [
    {
      "id": "mcp.tool.read.read_document_outline",
      "name": "read_document_outline",
      "category": "mcp.tool.read",
      "surface": "MCP server (read)",
      "description": "Map a document into addressable ordinal ranges…",
      "source": "src/doc_ray/entrypoints/mcp/structure.py:22",
      "signature": "read_document_outline(document_id: 'str', max_entries: 'int' = 50) -> …",
      "evidence": { "signals": ["documented", "runtime-registry",
                                "source-located", "tested"],
                    "score": 5, "test_references": 3 },
      "confidence": "high",
      "notes": ""
    }
  ]
}
```

---

## CLI reference

| Flag | Effect |
|---|---|
| `--format {md,json}` | Report format. Default `md`. |
| `--out PATH` | Write to `PATH` (parents created) instead of stdout. |
| `--summary` | Counts and integrity only; omit per-feature detail tables. |
| `--category NAME` | Restrict to a category. Repeatable. |
| `--min-confidence {high,medium,low}` | Drop records below the given band. Default `low` (keep all). |

**Exit codes.** `0` — every collector succeeded. `1` — at least one collector
failed. The report is still produced, the failure is named in the integrity
table with its exception text, and the coverage line states that the inventory is
partial. A broken surface degrades the report loudly; it never shrinks it
silently.

---

## Snapshot (v1.39.0, commit `e551bc0`)

Recorded here as a reference point, not a contract — rerun the tool for current
numbers.

| Category | Features | High | Test-corroborated |
|---|---:|---:|---|
| `config.variable` | 152 | 0 (capped) | 59 % |
| `webapp.route` | 60 | 60 | 61 % |
| `graphql.query` | 33 | 33 | 96 % |
| `graphql.mutation` | 33 | 33 | 100 % |
| `ops.script` | 30 | 0 (capped) | 46 % |
| `http.route` | 26 | 20 | 65 % |
| `mcp.tool.read` | 22 | 22 | 100 % |
| `job.kind` | 21 | 21 | 100 % |
| `mcp.tool.write` | 18 | 18 | 100 % |
| `ingest.format` | 11 | 11 | 90 % |
| `annotation.layer` | 9 | 7 | 77 % |
| `mcp.resource` | 8 | 8 | 100 % |
| `annotation.language` | 3 | 3 | 100 % |
| **Total** | **426** | **236** | **313** |

---

## Files

| File | Role |
|---|---|
| `scripts/gen_feature_inventory.py` | CLI, filtering, markdown/JSON renderers |
| `scripts/_feature_inventory_collectors.py` | The nine collectors |
| `scripts/_feature_inventory_model.py` | `Feature` record, scoring, corroboration index |
| `tests/test_feature_inventory.py` | 22 guards, each with a self-test proving it can fail |

Unlike the rest of `scripts/`, this tool **does** import `src/doc_ray/` and
`webapp/` — introspecting the live wiring is the entire method. It opens no
database connection and makes no network call: building a schema, importing a
tool tuple, and walking a route table are all in-process operations.

---

## Adding a collector

1. Write `collect_<surface>() -> CollectorResult` in
   `_feature_inventory_collectors.py`. Prefer a runtime registry; fall back to a
   declarative file only when none exists.
2. Emit one `Feature` per capability with a unique `id`, a `category`, a
   `source_file`/`source_line`, and `probes` — the identifiers a test would
   plausibly mention. Quote or parenthesise anything that doubles as an ordinary
   English word.
3. Set `signals=["runtime-registry"]` or `["static-declaration"]`. The remaining
   signals (`source-located`, `documented`, `tested`) are attached automatically
   by `apply_corroboration`.
4. Append the function to `COLLECTORS`.
5. Add a ground-truth anchor to `tests/test_feature_inventory.py` — a capability
   you know exists — so the new collector cannot pass by returning nothing.

Raising inside a collector is safe: the runner records the failure and continues.

---

## What this tool does not tell you

- **That a capability works.** A registered route, a bound handler, and a green
  identifier match are evidence of *existence and wiring*, not of correctness.
- **How good a description is.** Descriptions are the first docstring line. An
  undocumented capability is reported with one fewer signal, not a better summary.
- **Semantic coverage.** See the `tested` caveat above.
- **Anything about runtime data.** No corpus is queried; the inventory describes
  the software, not its contents.

---

## Tests

```bash
pytest tests/test_feature_inventory.py
```

The suite guards three failure modes: collector health (a surface that stops
being introspectable must fail loudly), ground-truth anchors (known capabilities
must appear, so collectors cannot pass vacuously), and evidence discipline (the
runtime cap holds, and short common fragments cannot corroborate anything). Each
rule is paired with a self-test proving it can fail.

---

[← scripts/](./README.md) · [← project README](../README.md)
