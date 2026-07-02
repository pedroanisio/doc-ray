---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-02"
---

# Request: Sync core docs after a delivery

## Inputs

- **Delivery**: `<diff range, feature name, or session summary>` — what was
  just built or changed. When omitted, derive it from `git status` and
  `git log` since the last release tag.

## Task

Work from ground truth, not recollection: before touching any document,
enumerate the delivered surface from the actual diff — new or changed
modules, CLI entrypoints, GraphQL queries/mutations, job kinds, database
schema objects, and configuration. This inventory drives every step below.

This repo is shared with concurrent agents: follow the `.agent-tasks/`
lock protocol before editing any file.

### 1. Features — `README.md`

Update the feature list, usage examples, and CLI/API surface to include the
delivery. Keep the mandatory `@DISCLAIMER.md` reference block intact — never
remove it.

**Gate**: every user-facing capability in the inventory appears in README;
every README claim is backed by code that exists on this branch.

### 2. Version and history — `CHANGELOG.md`

Add a conventional entry (`feat:` / `fix:` / `docs:` …) describing the
delivery. If the change warrants a SemVer bump, update the single enforced
version line (`doc_ray.__version__` — never hard-code versions elsewhere).

**Gate**: changelog entry matches the inventory; version, changelog, and
`__version__` agree.

### 3. Topic documents — `docs/`

Update only the documents the delivery actually touches:

- `architecture.md` — new components, entrypoints, or data flows.
- `DATABASE.md` / `DATABASE_SPEC.md` — any schema, index, or trigger change.
- `canonicalization_pipeline.md` — pipeline stages, job kinds, resolvers.
- `SHOWCASE.md` — new demonstrable capabilities.
- `BACKLOG.md` / `backlog.yml` — mark delivered items done; drop stale ones.

**Gate**: no touched document contradicts the code; untouched documents are
listed in the report as "no change needed", not silently skipped.

### 4. Prompt library — `docs/prompt-*.md`

These prompts state what Doc-Ray can do; the delivery may have changed that.
Update capability claims, named mutations/queries, and workflow steps so
every prompt matches the system as it now exists.

**Gate**: no prompt names a capability, tool, or endpoint that does not
exist on this branch.

### 5. Guidelines — `CLAUDE.md` (and `AGENTS.md` when present)

Update **only** if the delivery changed how agents must work in this repo
(new tooling, new invariants, new coordination rules). Feature work alone
does not belong in guidelines. Never remove or weaken existing constraints.

**Gate**: any guideline edit cites the concrete process change that
motivated it.

## Rules

- **Append or correct — never erase.** Do not delete caveats, disclaimers,
  or history to make docs look cleaner.
- **No speculative documentation.** Document only what was delivered and
  verified, not what is planned.
- Every edited Markdown file keeps (or receives) the mandatory disclaimer
  frontmatter; every README keeps its `@DISCLAIMER.md` reference.
- Do not commit unless the operator explicitly asked for a commit; when
  asked, use a `docs:` conventional commit.

## Acceptance criteria

- Every item in the delivered-surface inventory is reflected in at least one
  core document, or explicitly reported as intentionally undocumented and why.
- Every gate above passed, with evidence.
- Final report is a table: artifact → what changed → verification (or
  "no change needed"), so the operator can audit the sync at a glance.
