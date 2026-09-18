---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "doc-ray maintainers"
  date: "2026-09-18"
doc_type: readme
---

# scripts/ — standalone text tools

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

↑ Back to the [root README](../README.md).

Self-contained command-line tools, **independent of the production
`src/doc_ray/` pipeline and the database**. They read files directly and print to
stdout/JSON; nothing in `src/doc_ray/` or `webapp/` imports them. They live here
(not the repo root) to keep the root clean.

Run them from the repo root, e.g. `python scripts/extract_phrases.py file.pdf`.

Two tools are deliberate exceptions to the independence rule: `roundtrip.py` uses
the `doc_ray` package read-only, and `gen_feature_inventory.py` imports both
`src/doc_ray/` and `webapp/` because introspecting the live wiring is its whole
method. Neither opens a database connection.

| Script | What it does |
|---|---|
| `extract_phrases.py` | Read a document (txt/md/pdf/docx/epub/html), reflow lines wrapped mid-sentence, split into sentences one-per-line (+ optional stats, `--unique`, `--paragraphs`). |
| `docmodel.py` | **Lossless** hierarchical decomposition `document → section → heading/paragraph → sentence → phrase → word` with `compose(decompose(t)) == t`; JSON serialize + `--compose` to reconstruct. |
| `text_decomposer.py` | Self-contained `paragraphs → sentences → phrases → words` decomposer (rule-based, optional NLTK for grammatical phrase chunking). |
| `roundtrip.py` | Fidelity check: ingest a PDF → export → re-extract fresh and compare bytes / full_text / sentences. Uses the `doc_ray` package (read-only). |
| `gen_feature_inventory.py` | Mechanized capability inventory: enumerates GraphQL fields, MCP tools/resources, HTTP + webapp routes, job kinds, ingest formats, annotation layers, config variables and operator scripts from the live registries, with source locations and evidence-derived confidence. Markdown or JSON. See [README.feature_inventory.md](./README.feature_inventory.md). |
| `check_surface_parity.py` | Offline GraphQL-root inventory: reports every field as webapp-surfaced, explicitly allowlisted with a reason, or unsurfaced; `--strict` is the D-034 landing gate. |
| `run_capacity_model.py` | Analytical PostgreSQL capacity model at ~10k users / 1M documents: table/index sizing, QPS/latency/connection projections, vacuum/WAL/checkpoint pressure, bottlenecks, sensitivity ranking. Pure computation (no DB). See [capacity_model/README.md](./capacity_model/README.md). |
| `cost_telemetry.py` | Empirical counterpart to the capacity model: fits a per-stage cost model (`seconds = fixed + per_sentence × sentences`) from completed `jobs` rows, splits hours across the GPU/CPU worker lanes, measures storage per document, and projects the resource draw and cost of any corpus size. Unit prices come from [config/cost_rates.yaml](../config/cost_rates.yaml); unset rates report as unpriced rather than zero. Reads the live database. |
| `user_stats.py` | Who is actually registered: self-service signups (`principal_credentials`) split by verified/unverified/disabled, login-activity and signup windows (1d/7d/30d), live webapp sessions, the identity mix in `principals` (human vs service vs SSO-only vs SCIM-managed), a per-tenant breakdown, and the most recent signups with masked addresses (`--emails` unmasks). Text or `--json`. Read-only; reads the live database. |
| `showcase.sql` | Illustrative queries against the corpus. |

## Relationship to the pipeline

The production path is `src/doc_ray/` (extract → segment → annotate → Postgres,
via the hexagonal domain/ports/adapters/application/entrypoints layers) and
`webapp/` (browse the DB). These `scripts/` are earlier/auxiliary toolkit code
kept for standalone use; some concerns (sentence segmentation, lexeme identity,
PDF extraction) were reimplemented properly inside `src/doc_ray/` and are the
authoritative versions. The toolkit's own tests live in the top-level `tests/`
suite (`test_sentences`, `test_readers`, `test_cli`, `test_stats`, `test_docmodel`).
