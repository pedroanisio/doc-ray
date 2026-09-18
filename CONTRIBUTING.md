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

# Contributing to doc-ray

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](./DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

This document is the engineering contract for the repository: what the code
base is, how it is laid out, which standards a change must meet, and which gates
decide whether it lands. It applies equally to people and to coding agents
acting on their behalf; [`AGENTS.md`](./AGENTS.md) is the short form for agents.

## 1. What the project is

doc-ray is a living library and knowledge base (see [`PURPOSE.md`](./PURPOSE.md)
for the product identity and its Today/Becoming boundary). Concretely, it ingests
documents (PDF/EPUB/HTML/Markdown/TXT/DOCX/PPTX/SVG — the registry lives in
`domain/document_formats.py`) into a PostgreSQL-backed corpus (full text,
sentences, a 20-layer linguistic annotation graph, entity canonicalization to
shared identities, semantic enrichment, and the Substrate·Lens·Projection entity
fabric — genre classification, lens-scoped projections, cross-lens allusion
edges, and per-lens hyper-context rollups), stores original bytes
content-addressed in MinIO/S3, and exposes the result through a GraphQL API
(Strawberry), a read-oriented Flask webapp, and an MCP server for AI agents that
is read-only by default (opt-in write tools behind `DOCRAY_MCP_ENABLE_WRITE`).

```
.
├── CONTRIBUTING.md            # This file — the engineering contract
├── AGENTS.md                  # Short form for coding agents
├── DISCLAIMER.md              # Methodological caveats (all READMEs must reference)
├── PURPOSE.md                 # WHY the project exists (operator-owned)
├── README.md                  # WHAT the project does (usage, overview)
├── CHANGELOG.md               # Engineering changelog (Keep a Changelog)
├── docs/                      # Architecture, database dictionary, NLP layers, design records, public docs
├── db/migrations/             # Versioned SQL schema (docray_schema_version())
├── engineering_decisions.yml  # Validated ADR system of record
├── src/doc_ray/               # ports/adapters/application/domain/entrypoints (hexagonal)
├── webapp/                    # Flask browser over the corpus (read-only + thin GraphQL write path)
├── admin-console/             # React admin console
├── skills/                    # The agent-facing skill shipped with the product
├── scripts/                   # Generators, checkers, and standalone tooling
└── tests/, src/doc_ray/tests/ # The two test suites (D-013)
```

### PURPOSE.md takes precedence

- Never propose changes that conflict with `PURPOSE.md`.
- Do not justify decisions primarily by the current state of the code base;
  move it toward the target state `PURPOSE.md` describes, even when that means
  departing from the existing implementation.
- `PURPOSE.md` is the operator's product identity. Do not rewrite it or infer
  it from the code. Its Today/Becoming boundary is the operator's call: when a
  capability has shipped but is still listed under "Becoming", flag the drift and
  let the operator reclassify.

## 2. Releasable assets versus working material

The repository holds only what is needed to build, test, secure, deploy, operate,
maintain or use the product, plus accurate documentation of it. Plans, prompts,
agent instructions, exploratory findings, execution logs, progress reports,
measurement snapshots and session notes are **working material** and live
outside the tree, in the directory named by `DOCRAY_WORK_DIR`.

- Tools that produce scratch output (screenshots, benchmark results, evaluation
  reports, telemetry snapshots) write under `$DOCRAY_WORK_DIR`; when it is unset
  they fall back to the gitignored `_work/` directory at the repository root.
  Nothing under either location is ever committed.
- Tracked files never name a local path, a working directory, a session, or a
  plan phase. Documentation states what the product does and why; it does not
  narrate how a change was produced. A comment states the failure mode and the
  invariant it protects — not the date it was observed, the size of a corpus,
  or who decided.
- Deployment-specific values (hostnames, buckets, regions, account ids, private
  addresses) belong in the deployment's own environment files, never in
  samples, docs or tests. Samples use placeholders; tests use documentation
  addresses (`203.0.113.0/24`, `example.com`).
- `.gitignore` carries generic patterns only. A file that must exist locally but
  can never ship is listed in `.git/info/exclude`; an ignore rule never protects
  a file that is already tracked, which is why the gates below check the index.

These rules are enforced by the release-audit gates in §6.

## 3. LLM output verification — an architectural requirement

LLMs produce errors as a statistical property of the model class: omissions,
hallucinations, partial completions, schema violations and silent failures.
Every pipeline, agent or workflow in doc-ray that accepts model output treats it
as **untrusted, incomplete and unverified by default**, and verification is a
first-class design concern, on par with authentication and input validation.
Absence of a verification layer is a design defect, not a runtime bug.

The code base refers to this requirement as **PALS's Law**. A function that
calls a model documents the contract in its docstring; a caller that skips
output validation is introducing an architectural omission.

## 4. Core principles

1. **Fix root causes, never symptoms.** Investigate before patching. If a test
   fails, understand why — do not just make it pass.
2. **Test-driven development.** Red → Green → Refactor → Cleanup. Write the
   failing test first. No code ships without tests.
3. **Production-ready code only.** No placeholders, no deferred stubs. Every
   commit must be deployable.
4. **Quality regressions are fixed, not attributed.** When a regression is
   reported — visual, behavioural or metric — find the defect and fix it,
   without spending effort on which change or subsystem introduced it.

## 5. Development standards

### Testing

- 80% coverage for libraries, 60% for CLIs; the package gate is 90% (D-013).
- Unit, integration and end-to-end tests; deterministic, isolated and realistic.
- Run the relevant tests after every change — do not batch validation.
- Do not run the suite against a live stack; use `scripts/test-in-docker.sh`
  (see the README's *Dockerized test environment*).

### Code quality

- Typed errors in libraries, graceful handling in applications.
- Ruff (strict, incl. docstrings — D-011) and mypy strict (D-012); every source
  file under 400 net lines (D-015); no unnecessary dependencies.
- New code is Python (package, scripts, webapp) or TypeScript (admin console).
  JavaScript only where the runtime requires it (inline browser scripts).

### Documentation

- English (en-US) for code, comments, commits and documentation. Portuguese
  appears only in bilingual project-level documents (`DISCLAIMER.md`) or
  content that targets a pt-BR audience; English is then the primary text.
- Markdown for prose. Every tracked Markdown document carries YAML front matter
  with the `disclaimer` block (`notice`, `generated_by`, `date` — omitted only
  by generated documents that a drift gate diffs) and a
  `doc_type` (one of `reference`, `how-to`, `concept`, `explanation`, `runbook`,
  `design`, `policy`, `changelog`, `release-note`, `readme`). `generated_by`
  names the model or tool that produced the document and nothing else.
- Every `README.md` references `DISCLAIMER.md` with the block below, placed
  after the title (adjust the relative path to the file's depth):

  ```markdown
  ## Disclaimer

  This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](./DISCLAIMER.md).
  > No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.
  ```

- Public documentation (`docs/public/`) follows
  [`docs/documentation-governance.md`](docs/documentation-governance.md) and is
  gated by `scripts/check_public_docs.py`.
- Effort is expressed as complexity (XS / S / M / L / XL), never as time.

### Version control

- Conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`,
  `ci:`, `build:`, `perf:`. The subject is a sentence about the change, never a
  session, a checkpoint or "sync".
- Semantic versioning for the schema and the package, kept in lockstep
  (`scripts/check_version_consistency.py`).
- AI-generated artifacts are labelled with their source model/tool in front
  matter or metadata (file level, not per paragraph).

### Architecture decisions

- Significant decisions are recorded with rationale in
  `engineering_decisions.yml` (D-016), validated by the toolchain under
  `engineering/schema/`.
- When multiple approaches exist, state the trade-offs before proceeding; when
  scope is ambiguous, ask one targeted question.

## 6. Landing checklist

Run **all** gates before every commit (`make check`; `make check-static` skips
pytest). A gate that is already red on `main` is not licence to skip it: fix it
or record the exception in the same change — never widen an existing violation.

```bash
ruff check src scripts webapp && ruff format --check src scripts webapp  # D-011
mypy src                                                                 # D-012
python scripts/check_file_size.py src/doc_ray webapp scripts             # D-015
python scripts/check_tracked_imports.py                                  # no committed→uncommitted import
python scripts/check_version_consistency.py                              # version lockstep
python scripts/check_read_path_aggregates.py                             # D-024 read-path aggregates
python scripts/check_surface_parity.py --strict                          # D-034 GraphQL ↔ webapp parity
python scripts/gen_env_samples.py --check                                # env registry ↔ samples in sync
python scripts/check_public_docs.py                                      # public documentation drift
python scripts/check_doc_links.py                                        # Markdown links resolve
python scripts/check_doc_quarantine_policy.py                            # doc-hygiene output stays ignored
python scripts/check_touch_targets.py                                    # D-036 touch targets
python scripts/gen_data_dictionary.py --check                            # DATABASE.md matches the schema (needs a DB)
python skills/doc-ray-graphql-maximizer/scripts/check_doc_ray_drift.py   # shipped skill matches the GraphQL surface
python scripts/check_tracked_ignored.py                                  # C-1 index ↔ ignore policy
python scripts/check_private_references.py                               # C-2 no private material in tracked files
python scripts/check_doc_types.py                                        # C-3 every tracked doc declares a shippable type
python scripts/check_build_context.py                                    # C-4 Docker context is an allow-list
python scripts/check_secrets.py                                          # C-7 no credentials, keys or oversized files
python scripts/check_front_door.py                                       # C-8 README/CONTRIBUTING agree with the registry and hooks
python scripts/check_release_state.py                                    # canonical version and annotated tag
pytest tests src/doc_ray/tests                                           # D-013
```

`scripts/check_commit_message.py` runs as the `commit-msg` hook (C-6), and
`scripts/check_release_package.py` (C-5) runs in the release job, inspecting the
built wheel, sdist and image before a tag is cut.

### Release-audit gates

| Gate | Fails when | Remedy |
|---|---|---|
| C-1 `check_tracked_ignored.py` | a tracked file matches an ignore rule; a tracked symlink's target is untracked; anything is tracked under a directory reserved for local output (`out/`, `_work/`, `experiments/`, the quarantine or task-registry directories) | move to `$DOCRAY_WORK_DIR`, `git rm --cached` |
| C-2 `check_private_references.py` | a tracked text file contains a home path, a working-directory name, a managed-database hostname or private address, session narrative, a plan-phase label in docs, a dated diary comment in code, or cites `CLAUDE.md`/`AGENTS.md` from code | rewrite; state the invariant |
| C-3 `check_doc_types.py` | a tracked Markdown file outside `docs/public/` lacks a shippable `doc_type`, or declares `plan`/`proposal`/`report`/`finding`/`session`/`audit`/`brief`, a `proposed`/`draft`/`historical` status, or `retire-when:` | relocate to `$DOCRAY_WORK_DIR` or retype |
| C-4 `check_build_context.py` | `.dockerignore` is not an allow-list, or it would send env files, caches, editor state or working directories to the daemon | fix `.dockerignore` |
| C-5 `check_release_package.py` | the wheel contains `doc_ray/tests`, the sdist carries stray Markdown, or the image holds files outside `/app/{src,db,webapp}` or matching C-2 | fix packaging |
| C-6 `check_commit_message.py` | the subject is not a conventional commit, or the message carries a UUID, "session", "checkpoint", a bare "sync" or a local path | rewrite the message |
| C-7 `check_secrets.py` | a private key block, a provider token, a non-empty secret in a non-example env file, or a file over 5 MB outside `static/fixtures/` | remove and rotate |
| C-8 `check_front_door.py` | `README.md`'s ADR range disagrees with the registry; a hook script is missing from this checklist; a README fence is unmatched; a `generated_by` value is not a plain model/tool identifier | fix the document |

Narrow exceptions are recorded in `.release-audit-allow.txt` as
`RULE<TAB>path[:line]<TAB>reason`; an entry without a reason is rejected, and a
change adding more than three entries needs the documentation maintainer's
review.

### Human review for semantic contamination

Deterministic checks catch artifacts; reviewers catch meaning. For every change
that touches `docs/`, `CHANGELOG.md`, `README.md`, a migration, or adds a
comment longer than three lines, the reviewer answers:

1. Would this paragraph mislead a reader who has never seen the issue tracker, a working session, or the maintainers' infrastructure?
2. Does it state an invariant, or a diary entry? (A date, a corpus count, or "we found" is a diary entry.)
3. Does it name a phase, block, issue, model or person as the reason something exists?
4. Could a script regenerate it? Then the script's output should not be committed.
5. Is it about the product, or about producing the product?
6. Would it be acceptable in `docs/public/` tomorrow?

## 7. Document relationships

| Document | Audience | Defines |
|---|---|---|
| `DISCLAIMER.md` | Everyone | Epistemic integrity commitments |
| `CONTRIBUTING.md` | Contributors and their agents | HOW to build (process, standards, gates) |
| `AGENTS.md` | Coding agents | The short form of this contract |
| `PURPOSE.md` | Everyone | WHY the project exists (beliefs, principles) |
| `README.md` | Humans | WHAT the project does (usage, overview) |
| `engineering_decisions.yml` | Contributors | The validated ADR record |
| `docs/documentation-governance.md` | Documentation maintainers | What is public, and how it is written |
