---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Sonnet 5 via Claude Code (merge of Claude Opus 4.8 backlog + OpenAI Codex improvement-proposal candidates)"
  date: "2026-07-04"
---

# Backlog

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Deferred work — features, tech-debt, and known bugs — captured so it isn't lost.
Nothing here blocks current use.

**System of record:** [`docs/backlog.yml`](backlog.yml), structured by
[`docs/schema/backlog.schema.json`](schema/backlog.schema.json) (v1.0.0). The YAML
is canonical; this table is a rendered view of it. Effort is expressed as
**complexity** (XS–XL), never time (CLAUDE.md). `Owner` records the provenance
boundary so concurrent-session work stays attributable.

Validate the backlog against its schema:

```bash
pip install jsonschema pyyaml   # or: pip install -r requirements-dev.txt
python -c "import json,yaml,jsonschema; \
  jsonschema.validate(yaml.safe_load(open('docs/backlog.yml')), \
  json.load(open('docs/schema/backlog.schema.json')))"
```

or run the regression suite that keeps this document, `backlog.yml`, and
`engineering_decisions.yml` from drifting apart:

```bash
pytest src/doc_ray/tests/test_backlog.py
```

**2026-07-04 merge:** this backlog absorbed the 10 backlog-shaped candidates
(`BL-016`–`BL-025`) from `docs/doc_ray_improvement_proposal.md` — see that
document for the narrative gap analysis behind them. `BL-009`/`BL-010`/
`BL-011`/`BL-014` statuses were also reconciled against the live tree: they
had shipped but were still marked `parked`/`ready`.

**2026-07-04 UX audit:** added `BL-026`-`BL-036` from a webapp UX/UI audit (all `webapp` category; findings H1-H3, M1-M4, L1-L4).

**2026-07-04 refine pass (frontend):** promoted `BL-028`/`BL-031` to `ready`; de-duplicated `BL-029`/`BL-036` (definitions vs. the tooltip affordance) and cross-linked them; added `BL-037` (POS color-only accessibility gap, WCAG 1.4.1).

**2026-07-04 help:** completed `BL-027` with a persistent `/help` guide and contextual guide links from pipeline status, domain seeding, and backups.

**2026-07-04 ops:** added and completed `BL-038` (webapp operator page for `corpus_health`/`db_health`) as the `/operator/health` page gated by `DOCRAY_OPERATOR_HEALTH_TOKEN`, narrowing `BL-023`'s browser-page option to the webapp UI surface specifically.

**2026-07-05 webapp orientation:** completed `BL-026`, `BL-030`, and `BL-034` with landing-page value copy, a guarded seeding workflow, and visually separated admin navigation.

**2026-07-05 webapp clarity and operator contracts:** completed `BL-023`,
`BL-029`, and `BL-036` with an admin-gated operator-health JSON contract,
shared accessible tooltip affordances, and in-app glossary/page definitions.

**2026-07-05 reader and extraction calibration:** completed `BL-003`, `BL-005`,
and `BL-035` with env-tunable PDF heading thresholds, full-document reader
mode, single-POS focus, and user-facing tables that no longer foreground raw
opaque identifiers.

**2026-07-05 extraction/API verification:** completed `BL-001`, `BL-007`, and
`BL-008` with opt-in image-only PDF OCR, a shared ML/LLM output verification
port, and GraphQL read-adapter contract tests that keep SQL out of resolvers.

**2026-07-05 Postgres/RDBMS domain gap-analysis:** added `BL-039`-`BL-046`. A
session first learned PostgreSQL administration and relational-modeling
practice from doc-ray's own ingested corpus (an EDB "PostgreSQL Essentials
v16" course + *Clean Code Principles and Patterns* ch.9), then audited
doc-ray's own Postgres usage against that material. No defects were found in
the schema/query/transaction layers; the gaps found are deployment/operational
posture — default credentials on a published port, a no-op API authenticator,
no backup schedule/PITR, unconfigured connection pooling, and reactive-only
autovacuum tuning. `BL-039`/`BL-046` also close out GitHub issues #2/#3, whose
backlog entry had stalled on a blocked `.agent-tasks` task since 2026-07-04.
Also amended `BL-017` with confirming evidence (pgvector HNSW indexes
provisioned but unqueried).

**2026-07-05 API and security surfaces:** completed `BL-009`, `BL-017`,
`BL-021`, and `BL-012` with keyset GraphQL pagination, bounded NLP-layer and
canonical/review read fields, and database-authenticated per-principal LOGIN
role isolation. Also reconciled `BL-035`'s YAML status to the existing rendered
done state.

**2026-07-05 security and Postgres ops hardening:** completed `BL-039`,
`BL-040`, `BL-041`, and `BL-043` by pinning the pgvector/Postgres image digest,
binding Postgres to loopback, refusing default database passwords, adding
header-token API authentication with RLS-scoped anonymous requests, and wiring
explicit API connection-pool bounds.

**2026-07-05 backup and maintenance hardening:** completed `BL-042`, `BL-044`,
`BL-045`, and `BL-046` with scheduled backup enqueueing, retention pruning,
annotation-table autovacuum tuning, a db-health monitor path, an FTS
language-config drift guard, and a profile-gated on-demand ingester. Also
corrected stale status drift for `BL-002` and `BL-024`, which remain parked.

## Items

| ID | Item | Category | Type | Status | Cx | Priority | Owner |
|----|------|----------|------|--------|----|----------|-------|
| BL-001 | OCR pass for image-only PDFs | extraction | feature | done | M | high | shared |
| BL-002 | Math fidelity: recover equations as LaTeX via opt-in ML backend | extraction | feature | parked | L | medium | shared |
| BL-003 | Calibrate heading-detection thresholds per corpus | extraction | tech-debt | done | S | low | mine |
| BL-004 | Render Markdown in the reading pane | webapp | feature | done | S | medium | mine |
| BL-005 | Full-document scroll view + solo-POS focus mode | webapp | feature | done | M | low | mine |
| BL-006 | Materialize the remaining NLP annotation layers | nlp | feature | parked | XL | medium | concurrent |
| BL-007 | Verification layer for ML/LLM annotator output (PALS's Law) | nlp | infra | done | M | high | shared |
| BL-008 | Complete the GraphQL (Strawberry) driving adapter | api | feature | done | L | medium | concurrent |
| BL-009 | GraphQL safety: depth/complexity limits + keyset pagination | security | feature | done | M | high | concurrent |
| BL-010 | Background jobs via a Postgres queue (FOR UPDATE SKIP LOCKED) | ops | feature | done | L | medium | concurrent |
| BL-011 | jobs table + sync worker runtime | persistence | feature | done | M | medium | concurrent |
| BL-012 | Per-principal LOGIN roles for hostile-direct-SQL isolation | security | feature | done | L | medium | shared |
| BL-013 | Raise package coverage from 57% to the 90% gate | testing | tech-debt | done | L | high | shared |
| BL-014 | Fix 14 E501 line-length errors in gen_data_dictionary.py | tooling | tech-debt | done | XS | low | concurrent |
| BL-015 | Fix test_migrate row_factory KeyError against the live DB | persistence | bug | done | S | high | concurrent |
| BL-016 | NLP capability matrix and drift guard | docs | infra | done | S | high | shared |
| BL-017 | Layer-aware GraphQL read model | api | feature | done | L | high | concurrent |
| BL-018 | spaCy annotation provenance | nlp | infra | done | M | high | shared |
| BL-019 | Annotation output validation | nlp | infra | done | M | high | shared |
| BL-020 | Shared read surface or parity tests | api | tech-debt | done | M | high | shared |
| BL-021 | Canonicalization and review API surfaces | api | feature | done | M | high | concurrent |
| BL-022 | Annotation language and model capability registry | nlp | infra | done | S | medium | shared |
| BL-023 | Operator health surfaces | ops | feature | done | M | medium | shared |
| BL-024 | Evaluation and drift artifacts | nlp | research | parked | L | medium | shared |
| BL-025 | Lexeme/token projection invariant | persistence | tech-debt | done | M | medium | shared |
| BL-026 | Surface a product value proposition in the webapp | webapp | feature | done | S | high | shared |
| BL-027 | Add an in-app help / documentation / onboarding surface | webapp | feature | done | S | high | shared |
| BL-028 | Corpus landing: dynamic lede + empty state | webapp | bug | done | XS | high | shared |
| BL-029 | Define domain jargon across the webapp | webapp | feature | done | M | medium | shared |
| BL-030 | Seeding screen: workflow signposting and Seed confirmation guardrail | webapp | feature | done | S | medium | shared |
| BL-031 | Disclose that corpus search is a literal substring match | webapp | feature | done | XS | medium | shared |
| BL-032 | Humanize pipeline/job status outcomes and failures | webapp | tech-debt | done | M | medium | shared |
| BL-033 | Reader: 'not yet annotated' empty state | webapp | feature | done | S | low | shared |
| BL-034 | Group admin/destructive nav items away from reading | webapp | tech-debt | done | XS | low | shared |
| BL-035 | De-emphasize raw ULIDs in user-facing tables | webapp | tech-debt | done | XS | low | shared |
| BL-036 | Extend the reader's tooltip pattern to POS chips and status pills | webapp | feature | done | S | low | shared |
| BL-037 | Accessibility: part-of-speech is conveyed by color alone | webapp | bug | done | S | medium | shared |
| BL-038 | Webapp operator page: surface corpus_health / db_health in the UI | ops | feature | done | M | medium | mine |
| BL-039 | Pin the Postgres image to stop collation-version drift (GH#2) | ops | bug | done | S | high | shared |
| BL-040 | GraphQL API authentication is a documented no-op | security | feature | done | L | high | shared |
| BL-041 | Postgres port published on all interfaces with default credentials | security | bug | done | XS | high | shared |
| BL-042 | No scheduled backups, retention policy, or PITR/WAL archiving | ops | feature | done | M | medium | shared |
| BL-043 | API connection-pool sizing is unconfigured | persistence | tech-debt | done | XS | medium | shared |
| BL-044 | No proactive autovacuum/index tuning despite two prior cascade-delete incidents | persistence | tech-debt | done | M | high | shared |
| BL-045 | FTS read/write language-config symmetry isn't enforced by a test | testing | tech-debt | done | XS | low | shared |
| BL-046 | docker-compose ingester exit code 2 reads as a startup failure (GH#3) | ops | tech-debt | done | XS | low | shared |

See [`backlog.yml`](backlog.yml) for each item's full description, rationale,
acceptance criteria, dependencies, related decisions (`D-###`), and references.

---

[← Back to root README](../README.md)
