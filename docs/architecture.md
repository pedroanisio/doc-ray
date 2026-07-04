---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-04"
---

# Architecture

`doc-ray` is organized as a **hexagonal (ports & adapters)** application with
**DDD** layering. The importable core lives under `src/doc_ray/`; the frontend
(`webapp/`) and standalone tools (`scripts/`) sit **outside** `src/`.

```
                          driving adapter
                        ┌────────────────┐
                        │  entrypoints   │   python -m doc_ray.entrypoints.cli
                        │     (cli)      │
                        └───────┬────────┘
                                │ calls use-cases
                        ┌───────▼────────┐
                        │  application   │   pipeline · reannotate · reextract
                        │   (use cases)  │
                        └───────┬────────┘
             depends on ports   │   (interfaces, not concretions)
                        ┌───────▼────────┐
                        │     ports      │   TextExtractor · ObjectStore
                        │  (contracts)   │   Repository · Annotator
                        └───────┬────────┘
              implemented by    │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                        ▼
┌───────────────┐      ┌────────────────┐       ┌────────────────┐
│   adapters    │      │    adapters    │       │    adapters    │   driven adapters
│  extraction   │      │  persistence   │       │  object_store  │
│ (PyMuPDF/…)   │      │  (Postgres)    │       │   (MinIO/S3)   │
└───────┬───────┘      └────────────────┘       └────────────────┘
        │ uses
┌───────▼──────────────────────────────────────────────────────┐
│  domain  —  pure, dependency-free: pdf_layout, sentences,      │
│             lexemes, ulid, provenance                          │
└───────────────────────────────────────────────────────────────┘
```

**The dependency rule:** dependencies point inward. `domain` depends on nothing;
`adapters` depend on `domain`; `application` orchestrates via `ports`;
`entrypoints` drive the application. Nothing inward depends on a framework.

## Layers

| Layer | Package | Contents | May import |
|---|---|---|---|
| **Domain** | `doc_ray.domain` | `pdf_layout` (layout→Markdown), `sentences` (segmentation), `lexemes` (identity, tokenization, `RuleAnalyzer`, language detection), `ulid` (value object), `provenance` (events) | stdlib only |
| **Ports** | `doc_ray.ports` | `TextExtractor`, `ObjectStore`, `Repository`, `Annotator` — `Protocol` contracts | domain |
| **Adapters** | `doc_ray.adapters` | `extraction` (PyMuPDF + pypdf + txt/html/epub/docx), `persistence` (Postgres/psycopg), `object_store` (MinIO/boto3), `annotation` / `annotation_spacy`, `export`, `wikidata` / `wikipedia` (canonical-entity KB resolution), `queue_postgres` (`FOR UPDATE SKIP LOCKED` job queue) | domain, ports |
| **Application** | `doc_ray.application` | `pipeline` (ingest use case), `reannotate`, `reextract`, `jobs` (worker dispatch + payload-carried pipeline-chain automation), `term_scan` / `canonicalize` / `resolution` / `seed_suggest` / `canonical_seed` (entity canonicalization — see below), `backup` (corpus snapshot & restore), `corpus_health`, `db_health` (instance health), `db_analyzer` (multi-engine structure + data profiling) | ports, adapters, domain |
| **Entrypoints** | `doc_ray.entrypoints` | `cli`; `api` (GraphQL, Strawberry) and `worker` — the async-read / sync-write split every mutation runs through (see below); `migrate`, `export`, `annotate(_spacy)`, `reannotate`, `reextract`; the diagnostics CLIs `corpus_health` / `db_health` / `db_analyze` | application |
| **Config** | `doc_ray.config` | env-driven `Config` (shared kernel) | stdlib |

## Outside the hexagon

- **`webapp/`** — the Flask frontend. Reads Postgres directly; imports **nothing**
  from `doc_ray`. Every write (upload, domain-seeding decisions, backups) goes
  through the GraphQL API instead — one write path, never a direct queue/DB
  write from the browser tier. Intentionally kept outside `src/`.
- **`scripts/`** — standalone CLI tools (`extract_phrases`, `docmodel`, …),
  independent of the pipeline.

## API, worker, and the job queue

`entrypoints.api` is a Strawberry GraphQL app: reads resolve directly against
an async read adapter; every write **enqueues a job** onto a Postgres-backed
queue (`adapters.queue_postgres`, `FOR UPDATE SKIP LOCKED`, heartbeat +
exponential-backoff retry) and returns that job's status — never a
synchronous result. `entrypoints.worker` is the sync process that claims and
dispatches jobs (`application.jobs.run_once`, kind → handler); it runs
blocking work (spaCy, KB network calls, the Anthropic frontier resolver, the
backup/restore engine) off the API's event loop.

A job's payload can carry a `chain`: on success, `run_once` enqueues the next
kind for the same document with the remaining chain before returning —
deterministic and auditable in the `jobs` table, and the mechanism behind
`ingestDocument(pipeline: true)` reaching a fully annotated, canonicalized
document with no further calls.

## Entity canonicalization

A shared **canonical registry** (`canonical` + the `form_index` remissive
index) turns divergent mention surfaces into one canonical spine. The stages
that populate it: **Stage 0** seeds canonicals from Wikidata
(`canonical_seed`, or the curated `seed_suggest` webapp workflow — mining a
document's vocabulary and resolving each candidate to an exact identity via
its Wikipedia article title, never fuzzy search); **Stage 1.5** (`term_scan`)
deterministically finds registry-known vocabulary that NER never tags
(technical-literature nouns); **Stage 2** (`canonicalize`) auto-links
mentions the index already resolves confidently; **Stage 3** (`resolution`,
opt-in) sends only the remaining residue to a constrained-choice frontier
resolver (Claude), whose adapter validates every verdict against the offered
candidate set (PALS — see `CLAUDE.md`) before anything commits; ambiguous or
low-confidence verdicts land in `resolution_review` for a human. Full design:
[`canonicalization_pipeline.md`](canonicalization_pipeline.md).

## Ports & their adapters

| Port | Adapter |
|---|---|
| `TextExtractor` | `adapters.extraction.extract` — dispatch by extension; PDF via PyMuPDF (structured Markdown) with a pypdf fallback |
| `ObjectStore` | `adapters.object_store.ObjectStore` — MinIO/S3 blob store |
| `Repository` | `adapters.persistence` — Postgres data-access (documents, spans, sentences, lexemes, annotations) |
| `Annotator` | `domain.lexemes.RuleAnalyzer` (zero-dep) and `adapters.annotation_spacy.SpacyAnnotator` (real POS) |

The ports are `runtime_checkable` `Protocol`s, so adapters conform structurally —
no subclassing, and a substitute (e.g. a different object store or a math-aware
extractor) drops in without touching the domain or application.

## Backup & restore

A corpus snapshot is **app-native**, not a `pg_dump` shell-out — the worker image
carries no Postgres client. `application.backup` dumps every corpus table with
binary `COPY ... TO STDOUT (FORMAT binary)` (round-tripping `tsvector`,
`vector(768)`, `jsonb`, and array columns without a text re-encode) and bundles it
with every object-store blob into one `.tar.gz` archive plus a JSON manifest.
Restore replays the archive under `SET LOCAL session_replication_role = replica`,
so foreign keys and the full-text-search trigger defer and the dumped tsvectors
survive verbatim, then re-uploads the blobs.

Both directions run as worker jobs (`backup`, `restore`) enqueued by the
`createBackup` / `restoreBackup` GraphQL mutations — the same single write path as
every other mutation. The archive is streamed to clients through a plain
`GET /backups/{id}/download` route on the API, which the webapp proxies so the
browser tier never touches the object store directly. The operational `jobs`
queue and the `backups` catalog are excluded from every archive, so a restore
never clobbers the live queue or the index it is reading from.

## Database diagnostics

Two read-only diagnostic entrypoints sit beside the pipeline, neither touching the
corpus. `db_health` snapshots the Postgres **instance** — size, connection
pressure, long-running and lock-blocked queries, bloat — and derives findings with
pure functions over the snapshot (the rules are unit-testable without a database);
it complements `corpus_health`, which reports on document/job *content*.
`db_analyzer` is a self-contained, engine-agnostic package: it inspects structure
and profiles data, then renders Markdown + JSON. Its `Engine` port
(`db_analyzer/engines/base.py`) resolves through a name→class registry, so a new
backend is one module plus one registry line — the same ports-&-adapters shape as
the rest of the hexagon, scoped locally to the analyzer.

## Running

```bash
# tests use pyproject's pythonpath (src, ., scripts) — no install needed
python -m pytest tests/                 # session suite
python -m pytest src/doc_ray/tests/     # NLP-layer suite

# CLI (host): src/ must be importable
PYTHONPATH=src python -m doc_ray.entrypoints.cli <file-or-dir>
# in Docker the image sets PYTHONPATH=/app/src
```
