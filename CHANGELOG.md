---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-02"
---

# Changelog

All notable changes to doc-ray are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

doc-ray carries **one global version** covering both the application package
(`doc_ray.__version__`) and the database schema contract
(`docray_schema_version()`). They are kept in lockstep by
`scripts/check_version_consistency.py`, which fails the build on any drift.

## [Unreleased]

### Added
- **Scheduled backups and Postgres maintenance hardening** (OpenAI GPT-5 via Codex) —
  the Compose stack now runs a backup scheduler with retention pruning, exposes
  `db_health --watch` as an opt-in ops monitor, tunes autovacuum/analyze
  thresholds for high-churn annotation tables, prevents the on-demand ingester
  from starting during plain `docker compose up`, and adds an FTS language-config
  drift guard, closing `BL-042`, `BL-044`, `BL-045`, and `BL-046`.
- **Postgres/API security and pool hardening** (OpenAI GPT-5 via Codex) —
  Compose now pins the pgvector/Postgres image by digest, binds Postgres to
  loopback, and refuses unset/default database passwords. The GraphQL API now
  supports bearer or `X-DocRay-API-Token` principal mapping through
  `DOCRAY_API_TOKENS`, keeps anonymous HTTP requests RLS-scoped instead of
  superuser, and exposes explicit async pool bounds through `DOCRAY_API_POOL_*`,
  closing `BL-039`, `BL-040`, `BL-041`, and `BL-043`.
- **OCR fallback, resolver verification, and GraphQL read contracts** (OpenAI GPT-5 via Codex) —
  image-only PDFs can now opt into PyMuPDF/Tesseract OCR through
  `DOCRAY_PDF_OCR`, ML/LLM-style candidate-resolution output is verified before
  mapping, and the Strawberry read surface has contract tests proving resolver
  fields delegate through the read adapter instead of embedding SQL, closing
  `BL-001`, `BL-007`, and `BL-008`.
- **Reader scroll, POS focus, and PDF heading calibration** (OpenAI GPT-5 via Codex) —
  the PyMuPDF layout path now accepts corpus-specific heading thresholds through
  `DOCRAY_PDF_HEADING_*` environment overrides, the document reader can switch
  to a continuous full-document scroll with single-POS focus, and Lexicon/job
  tables no longer foreground opaque identifiers, closing `BL-003`, `BL-005`,
  and `BL-035`.
- **Operator health contract and webapp definitions** (OpenAI GPT-5 via Codex) —
  `/operator/health.json` now exposes an admin-gated `operator-health.v1`
  payload, POS chips and status pills share keyboard/touch-accessible
  definitions, and the webapp Help surface plus feature pages define core
  terms such as lexeme, occurrence, annotate, canonicalize, and domain
  seeding, closing `BL-023`, `BL-029`, and `BL-036`.
- **Reader and job-status polish** (OpenAI GPT-5 via Codex) —
  the reader now renders extracted Markdown headings and lists as structured
  blocks while preserving inline POS annotation links, explains
  not-yet-annotated documents in place, and the pipeline status page now maps
  raw job kinds/results/errors to human labels, outcome summaries, and recovery
  actions, closing `BL-004`, `BL-032`, and `BL-033`.
- **Webapp orientation and guardrails** (OpenAI GPT-5 via Codex) —
  the corpus landing page now states the ingest -> annotate -> curate value
  proposition, the seeding screen shows its workflow and confirms permanent
  seeding, and the masthead separates Backups from everyday reading
  navigation, closing `BL-026`, `BL-030`, and `BL-034`.
- **In-app help guide** (OpenAI GPT-5 via Codex) —
  the Flask browser now has a persistent `/help` guide reachable from every
  page, with contextual guide links from pipeline status, domain seeding, and
  backups, closing `BL-027`.
- **Operator health page** (OpenAI GPT-5 via Codex) —
  `/operator/health` now renders read-only corpus and database health snapshots
  in the Flask browser, grouping findings by `error`/`warning`/`info` and
  requiring `DOCRAY_OPERATOR_HEALTH_TOKEN`, closing `BL-038`.
- **Reader POS accessibility cues** (OpenAI GPT-5 via Codex) —
  annotated reader words now carry visible part-of-speech sigils, accessible
  POS labels, and non-color underline styles so POS is no longer conveyed by
  hue alone, closing `BL-037`.
- **Annotation output validation** (OpenAI GPT-5 via Codex) —
  `SpacyAnnotator` now validates span bounds, token ordinals, sentence
  references, dependency heads, entity/chunk/coreference spans, lexeme
  projections, and language/model support before persistence. Successful runs
  record validation status in job and provenance output, and invalid annotation
  graphs fail visibly before any run is committed, closing `BL-019`.
- **Corpus search semantics disclosure** (OpenAI GPT-5 via Codex) —
  the browser search page now states that it performs literal substring
  matching, and its zero-result state explains inflection misses while pointing
  to the GraphQL `fullTextSearch` alternative, closing `BL-031`.
- **Lexeme/token projection invariant** (OpenAI GPT-5 via Codex) —
  `SpacyAnnotator` now builds `lexeme_occurrences` through a named projection
  over non-null `tokens.lexeme_uid` rows, with a pure projection test and a live
  Postgres parity test proving browser/API lexeme counts stay aligned with the
  token layer, closing `BL-025`.
- **spaCy annotation capability registry** (OpenAI GPT-5 via Codex) —
  `SpacyAnnotator` now exposes a language/model registry with install status,
  supported layers, and explicit fallback policy. Unsupported languages fail
  clearly by default; operators can opt into fallback via
  `DOCRAY_SPACY_FALLBACK_POLICY=fallback`, and provenance records detected
  language, annotation language, model, policy, and fallback decisions, closing
  `BL-022`.
- **Read-surface parity guard** (OpenAI GPT-5 via Codex) —
  `tests/test_read_surface_parity.py` now keeps the async read adapter,
  GraphQL query resolvers, and Flask browser read shapes from drifting apart.
  `ReadRepository` now documents the complete public async reader contract,
  including batch loaders, document-scoped lexeme/KWIC/search filters, and
  entity read methods, closing `BL-020`.
- **spaCy annotation provenance** (OpenAI GPT-5 via Codex) —
  `SpacyAnnotator` now writes an `annotated` provenance event in the same
  transaction as its spaCy and lexeme annotation rows. The event records
  tool/version, language, per-layer counts, lexeme-occurrence counts, annotation
  ids, and replacement semantics; integration tests now prove success creates
  the event and write failures leave no visible annotation run, closing
  `BL-018`.
- **NLP capability matrix and drift guard** (OpenAI GPT-5 via Codex) —
  `docs/nlp_capability_matrix.md` now maps each of the 20 documented NLP layers
  across schema, writer, read adapter, GraphQL, browser, health, and test
  coverage. `src/doc_ray/tests/test_nlp_capability_matrix.py` keeps API-exposed
  rows tied to live Strawberry fields and writer-backed rows tied to production
  paths plus tests, closing out `BL-016`.
- **Coverage gate closed to 91%** (from 87.2%) — ~54 new tests across the job
  dispatcher, GraphQL mutations/queries, the backup engine, and the frontier
  resolver's SELECT-commit path (previously untested), closing out `BL-013`.
  No production-code changes; verified stable across repeated clean-cache runs.
- **Postgres instance health CLI** (`python -m doc_ray.entrypoints.db_health`) —
  reports on the database *instance* itself (size, connection pressure,
  long-running and lock-blocked queries, table bloat), complementing
  `corpus_health`'s view of document/job content. The issue rules are pure
  functions over a snapshot (unit-testable without a database); `--json` emits a
  machine-readable report; exit code `2` signals any warning/error-level finding.
- **Multi-engine database analyzer** (`python -m doc_ray.entrypoints.db_analyze`) —
  inspects a database's structure (schemas, tables, columns, keys, indexes,
  constraints, sizes) and profiles its data (row counts, null rates, distinct and
  duplicate candidates, numeric/date/text distributions, outliers), rendering
  Markdown + JSON reports (`db_analysis.md` / `.json`). Read-only by default
  (`--allow-write` to opt in); engines are pluggable through a registry
  (`application/db_analyzer/engines/`), with PostgreSQL shipping today. Flags:
  `--engine`, `--dsn`, `--schemas` / `--exclude-schemas`, `--structure-only`,
  `--output-dir`, `--config`, `--json`.
- **Migration runner row-factory regression coverage** (OpenAI GPT-5 via Codex)
  — `applied_filenames()` now has explicit coverage for tuple-style psycopg
  rows, guarding the BL-015 live-DB bug where migration filenames could be read
  through the wrong row access mode.

### Fixed
- **Corpus landing count and empty state** (OpenAI GPT-5 via Codex) —
  the webapp landing lede now reflects the live document count instead of
  hardcoding "Five documents", and an empty corpus shows an upload call-to-action,
  closing `BL-028`.
- `fullTextSearch` used a hardcoded `'english'` tsquery configuration
  regardless of a document's detected language, silently degrading search
  quality for unannotated (`language IS NULL`, `'simple'`-config `fts`) and
  non-English documents. The reader now parses the query with each
  document's own `docray_ts_config(language)`, matching the writer
  (migration 016). The regression test previously depended on two tiny
  fixture documents ranking inside the query's default top-25 by raw
  `ts_rank` — no longer reliable once the shared corpus grew past ~40 real
  documents — so it was rewritten around an invented word (Postgres's
  English config stems algorithmically, not from a dictionary, so it stems
  nonsense words identically to real ones), decoupling the test from
  corpus size entirely.

## [1.7.0] - 2026-07-05

### Added
- **Scheduled backup retention and maintenance guardrails** (OpenAI GPT-5 via Codex) —
  `backup-scheduler` now enqueues backup jobs on a configured cadence, prunes
  old backup archives from object storage and the backup catalog according to
  explicit retention settings, and documents WAL/PITR as out of scope for the
  app-native snapshot mechanism. Migration 034 sets per-table autovacuum and
  analyze thresholds on `tokens`, `dependency`, `lexeme_occurrences`, and
  `entity_mention`; migration 035 advances the unified schema/package version
  to `1.7.0`. The Compose ingester is profile-gated as a run-only service, and
  a static FTS guard now keeps read/write language config coupled through
  `docray_ts_config`, closing `BL-042`, `BL-044`, `BL-045`, and `BL-046`.

## [1.6.0] - 2026-07-05

### Added
- **Postgres/API runtime hardening** (OpenAI GPT-5 via Codex) —
  Compose now pins the pgvector/Postgres image by digest, binds Postgres to
  loopback, and refuses unset/default database passwords. The GraphQL API now
  resolves bearer or `X-DocRay-API-Token` credentials to principals through
  `DOCRAY_API_TOKENS`, keeps anonymous HTTP requests on the RLS-limited
  `docray_api` role instead of the trusted superuser connection, enforces
  write scope on mutations, and exposes explicit async pool bounds through
  `DOCRAY_API_POOL_*`. Migration 032 grants the API role access to its runtime
  queue/seed-suggestion surfaces and protects seed suggestions with
  document-scoped RLS; migration 033 bumps the unified schema/package version
  to `1.6.0`, closing `BL-039`, `BL-040`, `BL-041`, and `BL-043`.

## [1.5.0] - 2026-07-05

### Added
- **Keyset GraphQL pagination and layer-aware read surfaces** (OpenAI GPT-5 via Codex) —
  GraphQL list fields now page with returned cursors instead of offset
  arguments, and documents expose bounded annotation-run, token, dependency,
  chunk, coreference, and entity-link reads through adapter-backed resolvers,
  closing `BL-009` and `BL-017`.
- **Canonical registry and review read surfaces** (OpenAI GPT-5 via Codex) —
  the API now exposes canonical registry search, canonical forms, unresolved
  mentions, and resolution-review rows without direct SQL or job JSON
  inspection, closing `BL-021`.
- **Principal LOGIN-role isolation** (OpenAI GPT-5 via Codex) —
  migration 030 adds per-principal database-role binding and moves trusted API
  `app.principal_id` assertion to `docray_api`, so a `docray_client` direct-SQL
  actor cannot impersonate another principal by setting a GUC. Migration 031
  bumps the unified schema/package version to `1.5.0`, closing `BL-012`.

## [1.4.0] - 2026-07-04

A minor, backward-compatible release spanning two threads of work: **corpus
backup & restore**, and completing the **entity-canonicalization pipeline**
into a webapp-curable, self-chaining ingestion workflow. Four schema additions
land since 1.3.0 (migrations 025–028: seed suggestions, backups, and two
rounds of delete-cascade FK indexes), and migration 029 advances the schema
contract (`docray_schema_version()`) to match — the same one-global-version
discipline adopted in 1.3.0.

### Added
- **Backup & restore** — full-corpus snapshot and point-in-time restore, driven
  from the webapp (`/backups`) and the GraphQL API. A backup bundles a logical
  database dump (every corpus table via binary `COPY`, preserving `tsvector`,
  `vector(768)`, `jsonb`, and array columns exactly) together with every stored
  object-store blob into a single portable `.tar.gz` archive.
  - `db/migrations/026_backups.sql`: the `backups` catalog table — status
    lifecycle `creating → available | failed | restoring`, archive/table/blob/doc
    counts, and the `schema_version` captured at backup time.
  - GraphQL mutations `createBackup(note)` and `restoreBackup(backupId)`, each
    enqueuing background work through the single write path.
  - Worker job kinds `backup` and `restore`. The engine (`application/backup.py`)
    dumps under `REPEATABLE READ` and restores under
    `session_replication_role = replica`, so foreign keys and the FTS trigger
    defer and the dumped tsvectors are preserved verbatim. The live `jobs` queue
    and the `backups` catalog are excluded from both dump and restore, so a
    restore never clobbers the queue or the index it is reading from.
  - `ObjectStore.list_objects(prefix)` on the `ObjectStore` port and its MinIO/S3
    adapter, letting the engine enumerate blobs to bundle.
  - Webapp `/backups` screen: list archives, create (optional note), download, and
    restore (guarded by a destructive-action confirmation).
  - Archive downloads are served by the API (`GET /backups/{id}/download`) and
    proxied by the webapp, so the browser tier never needs direct object-store
    access — a host-vs-container `S3_ENDPOINT` mismatch cannot break downloads.
- **Stage 1.5 — registry-driven term scan** (`application/term_scan.py`) —
  NER only tags proper-noun-shaped spans, so a technical document's real
  vocabulary (lowercase common nouns like "uncial" or "parchment") never
  became a linkable mention no matter how well the canonical registry was
  seeded. The term scan closes the gap deterministically: it scans a
  document's text for surfaces the remissive index (`form_index`) already
  knows and emits `entity_mention` rows under a dedicated `layer='term'`
  annotation, which Stage 2 then links through the ordinary auto-link
  pathway — ambiguous forms still route to the frontier/review queue. Guards
  (word boundaries, longest-match-wins, a length/stopword floor, case-exact
  acronym matching, and a nominal-POS filter using the document's own token
  layer) keep it from linking verb usages of polysemous KB aliases. Runs as
  part of the `canonicalize` worker job, before Stage-2 auto-link.
- **Webapp-curable domain seeding** — the human-audit step for growing the
  canonical registry now runs end-to-end in the browser instead of ad-hoc
  host scripts. A `seed_suggest` worker job mines a document for candidate
  terms (unlinked mention surfaces + frequent noun lemmas) and resolves each
  to a Wikidata identity through its **Wikipedia article title**
  (`adapters/wikipedia.py`) — an exact-identity method that also refuses
  disambiguation pages outright, rather than fuzzy KB search's top-hit drift
  (e.g. the term "versal" resolving to the Wikidata item for *Versailles*).
  Candidates land in a new `seed_suggestion` staging table (migration 025;
  `pending → approved | rejected → seeded`, `seeded` final) for a curator to
  approve/reject with the description shown as the audit surface; a
  `seed_approved` job then seeds only what was approved through the existing
  `canonical_seed` path. New GraphQL surface: `seedSuggestions(documentId,
  status)` query, `suggestSeeds(documentId, limit)` /
  `decideSeeds(approveIds, rejectIds)` / `seedApproved(documentId)`
  mutations; new webapp screen `/doc/<id>/seeding`.
- **Webapp upload with automated pipeline chaining** — `/upload` posts a file
  through the GraphQL `ingestDocument` mutation, which now accepts a
  `pipeline` flag (default `true`) that enqueues `annotate` and
  `canonicalize` as a job-payload chain: each successful step enqueues the
  next for the same document, so an upload reaches a fully linked document
  unattended. New GraphQL surface: `annotateDocument(documentId, lang)`,
  `canonicalizeDocument(documentId, resolve)` mutations and a
  `jobs(documentId, limit)` query for a document's pipeline-progress view;
  new webapp screen `/job/<id>` (auto-refreshing status page, capped upload
  size, friendly 413). `handle_annotate` now also sets `documents.language`
  (firing the migration-016 FTS-rebuild trigger), so the previously-required
  extra `reannotate` pass for stemmed search is no longer needed.
  Upload-forwarding errors are logged server-side rather than reflected to
  the browser (avoids leaking internal API URLs/stack text).
- **Postgres instance liveness for the object store** — `ObjectStore.ping()`,
  a cheap read-only reachability probe (lists buckets), wired into the API's
  `/readyz` endpoint and the Docker healthchecks for both compose files.

### Fixed
- **`DELETE FROM documents` on a fully annotated document ran for minutes at
  100% CPU** — the delete cascades through `annotations → tokens →
  dependency`/`lexeme_occurrences`/`coref_chain`/`resolution_review`, several
  of which had no usable index on their FK column (only a composite index
  leading with the wrong column), forcing a sequential scan per deleted row.
  Two migrations add the missing FK-column indexes: `027_fk_cascade_indexes.sql`
  covers the annotations cascade (`dependency`, `coref_chain`,
  `resolution_review`); `028_document_delete_cascade_indexes.sql` covers the
  document cascade (`tokens`, `lexeme_occurrences`, and the still-empty
  NLP-layer tables, so they cannot regress into the same trap once
  populated). A 287-page book's delete dropped from 24+ minutes to seconds.
- **A PDF's extracted text could carry NUL (`\x00`) bytes**, which
  PostgreSQL `text`/`jsonb` columns categorically reject — one NUL failed an
  entire ingest after three retry attempts. All extractor output
  (`full_text`, `title`, `author`, metadata strings) is now sanitized at the
  single dispatcher chokepoint (`adapters/extraction.extract`), covering
  every supported format.
- **A file with no extractable text (e.g. a scanned, image-only PDF) ingested
  "successfully" as an empty document**, silently running the rest of the
  pipeline on nothing. `ingest_file` now fails loudly with a message naming
  the remedy ("no extractable text — scanned/image-only file? OCR required
  before ingest") and records a `failed` document row instead of an empty
  `ingested` one; the pipeline-chain guard also treats a non-`ingested`
  status as a stop signal, so a failed or duplicate ingest never advances to
  `annotate`/`canonicalize`.

### Changed
- Database schema version `1.3.0` → `1.4.0` (migration 029), reconciling the
  seed-suggestion (025), backups (026), and FK-cascade-index (027, 028) table
  and index additions into the global SemVer line — `doc_ray.__version__`,
  `engineering_decisions.yml` `source_schema_version`, this changelog, and
  `docray_schema_version()` all move together. Backup archives record
  the schema version at capture time and restore refuses a mismatch, so archives
  taken against `1.3.0` do not restore into a `1.4.0` database.

## [1.3.0] - 2026-07-02

Adoption of a single, enforced SemVer line. This release reconciles the packaging
version (previously a static `0.1.0`) and the database schema version (previously
`1.0.0`) into one global `1.3.0`, and adds the machinery that keeps every version
surface consistent from here on.

### Added
- Single source of truth for the project version: `doc_ray.__version__`.
  `pyproject.toml` derives its version from this attribute rather than hard-coding it.
- `scripts/check_version_consistency.py`: a stdlib-only, offline gate asserting that
  the engineering-decisions record, this changelog, and the latest schema migration
  all agree with `doc_ray.__version__`. Wired into pre-commit.
- GraphQL `version` query field and a `version` field on the `/health` response, so
  the running service reports its semantic version.
- `db/migrations/024_schema_version_1_3_0.sql`: advances the database schema version
  to `1.3.0`.

### Changed
- Database schema version `1.0.0` → `1.3.0`, reconciling three backward-compatible
  additions that had landed without a version bump (SemVer drift):
  migration 021 (canonical entity registry), 022 (form index), 023 (resolution store).
- `engineering_decisions.yml` `source_schema_version` `1.0.0` → `1.3.0`.

### Fixed
- GraphQL `ingestDocument` multipart uploads were rejected at the transport layer
  (Strawberry `multipart_uploads_enabled` defaults to `False`); the upload mutation
  is now reachable over HTTP.
- Upload ingestion no longer leaks the temporary scratch filename into a document's
  `title` / `original_filename`; the uploaded name and explicit `title` are preserved.

[1.7.0]: https://semver.org/spec/v2.0.0.html
[1.6.0]: https://semver.org/spec/v2.0.0.html
[1.5.0]: https://semver.org/spec/v2.0.0.html
[1.4.0]: https://semver.org/spec/v2.0.0.html
[1.3.0]: https://semver.org/spec/v2.0.0.html
