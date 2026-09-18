---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 (1M context) via Claude Code"
  date: "2026-09-18"
doc_type: policy
status: current
---

# AGENTS.md — the short form for coding agents

Read [`CONTRIBUTING.md`](./CONTRIBUTING.md) once; this page is the part you need
on every turn.

## Layout

`src/doc_ray/` is the hexagonal package (`domain/`, `ports/`, `adapters/`,
`application/`, `entrypoints/`); `webapp/` is the Flask browser; `admin-console/`
is the React console; `db/migrations/` is the versioned schema;
`engineering_decisions.yml` is the ADR record; `docs/public/` is the published
documentation and `docs/` the engineering documentation; `scripts/` holds the
gates and tooling; `skills/` is the shipped agent skill.

## Non-negotiables

- `PURPOSE.md` wins over the current code. Never edit it; flag drift instead.
- Model output is untrusted until verified (PALS's Law, CONTRIBUTING §3).
- TDD: failing test first. No placeholders, no deferred stubs.
- Working material — plans, findings, logs, reports, screenshots, benchmark
  output, session notes — goes to `$DOCRAY_WORK_DIR` (fallback: `_work/`), never
  into `docs/` or the root, and never into a commit.
- Tracked files never carry local paths, working-directory names, session
  narrative, plan-phase labels, deployment hostnames or dated "measured on …"
  comments. State the invariant.
- Every tracked Markdown document has the disclaimer front matter and a
  `doc_type`; every README references `DISCLAIMER.md`.
- Conventional commit subjects; effort in complexity (XS–XL), never time.

## Before you land

```bash
make check          # every gate incl. pytest
make check-static   # the same without pytest
```

See CONTRIBUTING §6 for what each gate checks and how exceptions are recorded.
