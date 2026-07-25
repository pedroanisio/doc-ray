# doc-ray

doc-ray is a **living library and knowledge base**: it ingests documents into a
queryable, linguistically-annotated corpus — stored on **PostgreSQL** (metadata +
full text) and **MinIO / S3** (original bytes) — and resolves the people, places,
dates, and organizations inside them into shared canonical identities that connect
concepts across the whole corpus, exposed to people, applications, and AI agents.
See [`PURPOSE.md`](./PURPOSE.md) for why it exists and where it is headed.

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](./DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

New to doc-ray? [`docs/TUTORIAL.md`](docs/TUTORIAL.md) walks through a fresh
install end to end — clone, bring up the stack, ingest a document, query it
through the GraphQL API, and install the repo's agent-facing skill so an AI
coding agent (Claude Code, Codex) can explore the corpus correctly. The
sections below are the terser command reference.

## Architecture

```
             ┌────────────┐   extract text/meta   ┌──────────────────┐
  file(s) ─► │  ingester  │ ────────────────────► │ Postgres         │
  (.pdf)     │  (Python)  │   documents + FTS      │  documents       │
             │            │                        │  document_content│
             └─────┬──────┘                        └──────────────────┘
                   │ put original bytes (content-addressed by sha256)
                   ▼
             ┌────────────┐
             │  MinIO/S3  │  s3://documents/<ab>/<sha256>.pdf
             └────────────┘
```

- **Postgres** holds queryable metadata (`documents`) and extracted text with a
  generated `tsvector` for full-text search (`document_content`). Blobs never
  live in a row — only an `s3://` pointer + sha256 checksum.
- **MinIO** stores the original file bytes, keyed by content hash (dedupe + integrity).
- **ingester** computes the hash, uploads the blob, extracts text/metadata, and
  writes one transaction to Postgres. Re-ingesting identical bytes is a no-op.

## Quick start

```bash
cp .env.example .env
# Edit .env before starting: set POSTGRES_PASSWORD to a real secret.
# Compose refuses the historical default (`docray`) and placeholder values.

# 1. Bring up Postgres + MinIO (schema auto-applies on first init)
docker compose up -d postgres minio createbucket

# 2. Build the ingester image
docker compose build ingester

# 3. Ingest the sample PDF (repo root is mounted at /data)
docker compose run --rm ingester /data/static/fixtures/AirFrance447_BEA.pdf

# ...or a whole directory, recursively
docker compose run --rm ingester /data
```

The `ingester` service is profile-gated as an on-demand command, so plain
`docker compose up` does not start it or emit an argument-error banner.

Expected output:

```
Ingesting 1 file(s) into postgres:5432/docray ...
  [OK ] AirFrance447_BEA.pdf -> 3f2a...uuid
Done: ingested=1
```

## Verify

```bash
# Row landed with page count + status
docker compose exec postgres psql -U docray -d docray \
  -c "SELECT title, doc_type, page_count, status, source_uri FROM documents;"

# Full-text search over extracted content
docker compose exec postgres psql -U docray -d docray -c \
  "SELECT d.title, ts_rank(c.fts, q) AS rank
     FROM document_content c
     JOIN documents d ON d.id = c.document_id,
          plainto_tsquery('english', 'Big Brother') q
    WHERE c.fts @@ q
    ORDER BY rank DESC;"

# Metadata / structured query (your primary access pattern)
docker compose exec postgres psql -U docray -d docray -c \
  "SELECT title, author, metadata->>'pdf_producer' AS producer
     FROM documents WHERE doc_type = 'pdf' ORDER BY created_at DESC;"
```

MinIO console: http://localhost:9001 (login from `.env`).

## GraphQL API & webapp

Beyond the batch `ingester` CLI, the stack ships a GraphQL API (Strawberry)
and a Flask browser over the corpus:

```bash
docker compose up -d postgres minio createbucket api worker backup-scheduler webapp
```

`api` serves GraphQL at `http://localhost:8000/graphql`; `worker` is the
background job processor everything below enqueues onto (`FOR UPDATE SKIP
LOCKED`, retried with backoff). Uploading through the API is the primary,
webapp-driven ingestion path — the pipeline runs unattended from there:

```graphql
mutation($f: Upload!) {
  ingestDocument(file: $f, pipeline: true) { id status }   # pipeline: true is the default
}
# on success, enqueues annotate -> semantic_enrich -> canonicalize as a job-payload chain;
# poll pipeline progress with: { jobs(documentId: "...") { kind status } }
```

For push-based progress, the API also serves Server-Sent Events at
`GET /jobs/events`. Use the same bearer token or `X-DocRay-API-Token` header as
GraphQL, then filter with `document_id=<uuid>` or `job_id=<ulid>`:

```bash
curl -N -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/jobs/events?document_id=PASTE_DOCUMENT_UUID"
```

Events are emitted from Postgres job-status transitions and are re-read through
the caller's RLS-bound API connection before streaming. The nginx TLS front door
has a dedicated `/jobs/events` location with response buffering disabled; add
`once=true` for a finite initial snapshot in diagnostics or smoke tests.

Other query/mutation surface: `documents(docType:, status:, tags:)` /
`document` / `documentShares` / `documentMetadataHistory` / `searchSentences` /
`semanticSearchSentences` / `fullTextSearch` /
`corpusManifest(shardCount:)` / `corpusShard(shardIndex:, shardCount:)`
(deterministic corpus partitioning for parallel/map-reduce reads) /
`documentOutline(documentId:, maxEntries:)` (a document's addressable ordinal
ranges — detected headings, or an even grid where a document has none, with
`source` saying which) /
`lexeme(s)` / `entities` / `entityFrequency` (raw NER rows) /
`entityProfiles(documentId:, entityType:)` (the same mentions reconciled to one
row per entity, with the measured type spread and `typeConsistency`) /
`termFrequency(terms:)` (a flood-risk pre-flight: how common each candidate
search term is, so an agent picks a rarer one before searching) /
`job` / `jobs` /
`documentMetadataSuggestions` / `collections` / `collection` /
`lenses` (the collections marked as entity-resolution scopes — Block B) /
`documentImages` / `imageClassSummary` / `visualCaptions` /
`crossModalImageSearch` (Prism visual-layer reads) / `billingStatus` /
`billingEvents` (reads);
`createCollection` / `addDocumentToCollection` / `removeDocumentFromCollection`
/ `updateCollection` / `deleteCollection`
/ `promoteCollectionToLens(collectionId:, parentLensId:)` /
`demoteLens(collectionId:)` / `setDocumentDefaultLens(documentId:, lensId:)`
(Lens Registry — mark a collection an entity-resolution scope; see
[Lenses: naming entity-resolution scopes](#lenses-naming-entity-resolution-scopes)
below)
/ `reannotate` / `deleteDocument` / `archiveDocument` / `restoreDocument` /
`bulkArchiveDocuments` / `bulkTagDocuments` / `annotateDocument` /
`reextractDocument` / `reextractDocuments` / `canonicalizeDocument(resolve:)` /
`semanticEnrichDocument` / `embedDocumentSentences` / `embedCorpusSentences` / `updateDocumentMetadata` / `suggestDocumentMetadata`
/ `decideDocumentMetadataSuggestions` / `shareDocument` /
`revokeDocumentShare` / `setDocumentVisibility` / `cancelJob` /
`createBillingCheckoutSession` / `createBillingPortalSession` /
`retryBillingEvent` (pipeline
writes — every mutation enqueues a worker job or records a curator decision;
poll `job(id)` for completion, never a synchronous pipeline result). Entity
canonicalization curation: `seedSuggestions` / `canonical` /
`canonicalSearch` / `unresolvedMentions` / `resolutionReview` (reads),
`suggestSeeds` / `decideSeeds` / `seedApproved` (writes). NER correction —
the human verification layer over the model's entity output:
`entityMentionCorrections(documentId:)` (read the audit log) and
`correctEntityMention(documentId:, action:, charStart:, charEnd:, …)` (write;
`action` is `reclassify` / `reject` / `add` / `adjust_span`). Corrections are
stored against a stable span identity, so they are re-applied on every
re-annotation instead of being overwritten by it — see
[Correcting the model's entities](#correcting-the-models-entities) below.
Backup/restore:
`createBackup` / `restoreBackup` (see [Backup & restore](#backup--restore)
below). Security audit readback: `auditEvents` (admin-scoped).

API authentication is token-to-principal based. Set
`DOCRAY_API_TOKENS='token=principal_uuid[:read+write]'` to map bearer tokens (or
`X-DocRay-API-Token`) to rows in `principals`. Unauthenticated requests are
allowed by default for local/single-operator use, but they still drop to the
RLS-limited `docray_api` role with no principal id; set
`DOCRAY_API_ALLOW_ANONYMOUS=false` to fail closed with HTTP 401. API pool bounds
are explicit through `DOCRAY_API_POOL_MIN_SIZE`, `DOCRAY_API_POOL_MAX_SIZE`, and
`DOCRAY_API_POOL_TIMEOUT`.

Provision principals and tokens with the `auth_admin` CLI. Load the deployment
settings first — the CLI reads `DATABASE_URL` and otherwise falls back to the
factory default (`localhost:5432`), not your stack's real port/password:

```bash
set -a; source .env; set +a

# 1) Create a service identity; note the printed principal UUID
python3 -m doc_ray.entrypoints.auth_admin principal create \
  --display-name "claude-agent" --kind service --role reader

# 2) Mint a read-scoped token bound to it (paste the UUID from step 1)
PRINCIPAL_ID="00000000-0000-0000-0000-000000000000"
python3 -m doc_ray.entrypoints.auth_admin token mint \
  --principal "$PRINCIPAL_ID" --scopes read
```

`tenant create`, `grant document`, and `db-login create` cover the rest of the
identity workflow; every subcommand accepts `--database-url` to target another
database.

Local self-service auth is opt-in. Set `DOCRAY_AUTH_LOCAL_ENABLED=true` for
email/password login and `DOCRAY_AUTH_REGISTRATION_ENABLED=true` to expose
self-registration. Registered accounts are created as verified-only-after-email
reader principals; admin remains a seeded/operator-managed role. Document and
data visibility still follows the same Postgres RLS policies after login. Set
`DOCRAY_API_JWT_SECRET` so `/auth/login` can mint short-lived API access tokens;
leave `DOCRAY_AUTH_EXPOSE_VERIFICATION_TOKEN=false` outside local diagnostics
and deliver verification tokens by email. When local, OIDC, or SAML web auth is
configured, anonymous browser requests are redirected to login and do not fall
back to `DOCRAY_PRINCIPAL_ID`; set `DOCRAY_WEBAPP_REQUIRE_AUTH=true` to enforce
the same boundary explicitly.

The webapp (`docker compose up -d webapp`, serving on
`http://127.0.0.1:5001`; or locally `flask --app webapp.app run --port 5001`)
is a read-only Postgres browser plus a thin GraphQL write path: upload
a document, watch its pipeline status, read it with POS/entity overlays, and
curate **domain seeding** — mining a document's vocabulary, resolving each
term to an exact Wikidata identity, and auditing the resolved description
before anything reaches the shared canonical registry (the fuzzy-search
alternative silently drifts, e.g. resolving "versal" to the Wikidata item
for *Versailles*). Full walkthrough of both human and agent surfaces — the web
interface screen by screen and the MCP tool surface AI agents work the corpus
through: [`docs/webapp_user_manual.md`](docs/webapp_user_manual.md).
Entity canonicalization design: [`docs/canonicalization_pipeline.md`](docs/canonicalization_pipeline.md).

For HTTPS, `docker compose up -d nginx` starts a TLS front door on
`https://localhost:${NGINX_HTTPS_PORT:-443}` (plain HTTP on
`${NGINX_HTTP_PORT:-80}` 301-redirects there). A self-signed certificate is
bootstrapped on first start — expect a browser warning until you replace it
with a real one. Keep exposed ports inside a trusted network even when login is
enabled, because backup/restore and operator workflows can mutate the corpus.

### Correcting the model's entities

Named-entity recognition is machine output, so it is wrong some of the time —
a character mistyped as an organization, a date that isn't one, a name whose
span stops one word short. The corpus therefore treats a human correction as
first-class, durable data rather than a manual edit to a derived table.

This matters because `entity_mention` rows are *rebuilt from scratch* by every
annotation run. A fix written directly into them would be silently discarded the
next time the document is re-annotated. Corrections are instead recorded against
a **stable span identity** — the document plus the character offsets — and
re-applied by the pipeline on every pass. Fixing something once fixes it
permanently.

Four actions cover the ways the model gets an entity wrong:

| Action | Use when | Required |
|---|---|---|
| `reclassify` | The span is an entity, but of the wrong type | `entityType` |
| `reject` | The span is not an entity at all | — |
| `add` | A real entity the model missed | `entityType` |
| `adjust_span` | Right entity, wrong boundaries | `newCharStart`, `newCharEnd` |

Record one through the API — `originalType` and `rationale` are stored so every
override is attributable and reversible:

```graphql
mutation {
  correctEntityMention(
    documentId: "…", action: "reclassify",
    charStart: 16, charEnd: 22, entityType: "GPE",
    originalType: "PERSON", rationale: "London is a place, not a person"
  ) { id kind status }
}
```

The mutation returns a queued `apply_corrections` job (poll `job(id)`); the
worker re-applies the whole correction log onto the document's current mentions.
Read the log back with `entityMentionCorrections(documentId:)`. AI agents reach
the same capability through the MCP tools `correct_entity_mention` and
`entity_mention_corrections` (see [`docs/mcp-service.md`](docs/mcp-service.md));
the write tool is registered only when `DOCRAY_MCP_ENABLE_WRITE` is set.

## Lenses: naming entity-resolution scopes

A **lens** is a *named scope* under which entity identity is interpreted. The
corpus mixes fiction and non-fiction at scale, and a single global identity
registry is wrong for fiction: the *Duncan Idaho* of one novel must not collapse
into an unrelated book's Duncan. The fix is to make identity relative to a named
scope — a lens — rather than to fragment the registry. The reserved **`global`**
lens holds real-world (Wikidata-linkable) identities shared corpus-wide; a *scope
lens* groups the documents whose fictional identities belong together.

A lens is not a new structure — it is a `document_collection` you **promote**.
This block (Block B) ships the vocabulary only: it does **not** change how any
entity resolves today; a later projection layer (Block D) will consume it. See
[`docs/proposals/entity-fabric-substrate-lens-projection.md`](docs/proposals/entity-fabric-substrate-lens-projection.md).

Promote a collection, optionally set a document's default lens, and read the
scopes back:

```graphql
# 1. Promote an existing collection to a lens.
mutation { promoteCollectionToLens(collectionId: "01JADR…") { id name isScope } }

# 2. A document that belongs to the lens can name it as its default.
mutation {
  setDocumentDefaultLens(documentId: "01JADQ…", lensId: "01JADR…") {
    id defaultLensId
  }
}

# 3. List the scopes (the reserved `global` lens is always present).
query { lenses(limit: 50) { id name isScope parentLensId documentCount } }
```

Rules are enforced in the database: a default lens must be a scope the document
is a member of, a parent lens must itself be a scope, the parent graph may not
cycle, and `global` cannot be renamed, demoted, or deleted. `demoteLens` refuses
while any document still defaults to the lens. AI agents reach the same surface
through the MCP tools `list_lenses`, `promote_collection_to_lens`, `demote_lens`,
and `set_document_default_lens` (the write tools require `DOCRAY_MCP_ENABLE_WRITE`).
Promoting a collection is a **curation decision**, not a mechanical one — an
ordinary collection stays identity-neutral.

## Backup & restore

The whole corpus — every Postgres table plus every stored blob — can be captured
as a single portable `.tar.gz` archive and restored later. Backups run as
background jobs and are catalogued in the `backups` table.

From the webapp, open **Backups** (`/backups`) to create, download, or restore an
archive. From the GraphQL API:

```graphql
mutation { createBackup(note: "before re-ingest") { id status } }
mutation { restoreBackup(backupId: "01JADR…") { id status } }
```

Both enqueue a worker job — poll `job(id)` for completion. The dump uses binary
`COPY` (preserving `tsvector`, `vector`, `jsonb`, and array columns exactly) and
restore runs under `session_replication_role = replica` so triggers and foreign
keys defer. Restore is **destructive**: it rewinds the entire corpus to the
snapshot (the live job queue and the backup catalog are excluded, so in-flight
work and the archive index survive), and it requires the archive's
`schema_version` to match the target database.

The Compose stack also runs `backup-scheduler` by default when the full service
set is started. It enqueues a backup job every
`DOCRAY_BACKUP_SCHEDULE_SECONDS` seconds and prunes old backup archives after
`DOCRAY_BACKUP_RETENTION_DAYS`, while always retaining the newest
`DOCRAY_BACKUP_RETENTION_COUNT` archives. PITR/WAL archiving is intentionally
not enabled by this app-native snapshot scheduler.

## Database diagnostics

Two read-only CLIs inspect the database without touching the corpus:

```bash
# Postgres INSTANCE health — size, connection pressure, long-running / lock-blocked
# queries, table bloat. Exit code 2 on any warning/error-level finding.
python -m doc_ray.entrypoints.db_health           # text report
python -m doc_ray.entrypoints.db_health --json     # machine-readable
python -m doc_ray.entrypoints.db_health --watch 300 # exit non-zero on warning/error

# Structure inspection + data profiling → Markdown + JSON reports. Read-only by
# default (--allow-write to opt in); engines are pluggable (PostgreSQL today).
python -m doc_ray.entrypoints.db_analyze --output-dir ./reports
python -m doc_ray.entrypoints.db_analyze --structure-only --schemas public
```

`db_health` reports on the Postgres server itself; `corpus_health` covers
document/job *content*. `db_analyze` is engine-agnostic — add an engine under
`application/db_analyzer/engines/` to point it at another database.
In Compose, `docker compose --profile ops up -d db-health-monitor` runs that
watch mode as a restartable alert surface.

## Running the ingester on the host (no docker for Python)

```bash
pip install -r requirements.txt
# DATABASE_URL / S3_ENDPOINT default to localhost — see .env.example
python -m doc_ray.entrypoints.cli static/fixtures/AirFrance447_BEA.pdf
```

For object storage outside local MinIO, use an `https://` `S3_ENDPOINT`. A
plaintext `http://` endpoint emits a runtime warning unless
`S3_ALLOW_INSECURE_ENDPOINT=1` is set as an explicit local-development opt-in.
Set `S3_SERVER_SIDE_ENCRYPTION=AES256` or `aws:kms` to request S3 server-side
encryption on writes; use `S3_SSE_KMS_KEY_ID` with KMS-backed encryption.

PDF extraction defaults to PyMuPDF with a pypdf fallback. Math-heavy corpora can
opt into an external Docling/Marker/Nougat-style backend by setting
`DOCRAY_PDF_MATH_BACKEND=external` and `DOCRAY_PDF_MATH_BACKEND_CMD`; that
command must read PDF bytes on stdin and emit JSON containing LaTeX-bearing
`full_text`. Output without explicit LaTeX equation markup is rejected before it
can be persisted.

Deterministic semantic enrichment for the schema-backed discourse, temporal/event,
and sentiment layers runs in the default upload pipeline after token annotation and
before canonicalization. Operators can also queue it explicitly via GraphQL
`semanticEnrichDocument` or run it from the host:

```bash
python -m doc_ray.entrypoints.annotate_semantic --all
```

**Semantic sentence embeddings (NLP Layer 20)** are a separate, learned capability.
After the hard cutover from the non-learned `hash-bow-768` placeholder, sentences
are embedded by a sentence-transformers model behind the `TextEncoder` port, run by
the opt-in `semantic_embed` worker job (GraphQL `embedDocumentSentences` per document,
or the admin `embedCorpusSentences` to re-embed the whole corpus in one shot).
The encoder ships in the base image (pinned in `requirements.txt` next to the Prism
`open_clip_torch` encoder, on the `torch`/`transformers` stack that `spacy` already
pulls in), so a stock worker can run the job out of the box; the `.[semantic]` extra
is kept only as a convenience alias for installing it by name. It runs on the GPU
automatically when one is available: `make up` assesses the host
(`scripts/deploy_env.py`) and layers `docker-compose.gpu.yml` — GPU workers when
possible, CPU otherwise — while heavy `semantic_embed` jobs are routed off the
`worker-fast` pool so light user jobs never queue behind them. Force a device with
`DOCRAY_SEMANTIC_DEVICE=cpu|cuda|cuda:N` (default `auto`). Retrieval is then available by
*meaning* — GraphQL `semanticSearchSentences` and the `semantic_search_sentences`
MCP tool rank sentences by learned cosine similarity, complementing keyword
`searchSentences`.

Evaluation snapshots and lexical/entity drift metrics can be recorded with:

```bash
python -m doc_ray.entrypoints.evaluate --source fixture --json
```

## Layout

| Path                       | Purpose                                            |
|----------------------------|----------------------------------------------------|
| `docker-compose.yml`       | Postgres + MinIO + bucket-init + ingester services |
| `db/migrations/`           | Schema, indexes, triggers (auto-applied)           |
| `src/doc_ray/`             | The core package — **hexagonal / DDD** layout (below) |
| `src/doc_ray/domain/`      | Pure logic & value objects: `pdf_layout`, `sentences`, `lexemes`, `ulid`, `provenance` |
| `src/doc_ray/ports/`       | Boundary contracts: `TextExtractor`, `ObjectStore`, `Repository`, `Annotator` |
| `src/doc_ray/adapters/`    | Driven adapters: `extraction` (PyMuPDF/pypdf/opt-in external math backend), `persistence` (Postgres), `object_store` (MinIO), `annotation`/`annotation_spacy`, `wikidata`/`wikipedia` (KB resolution), `queue_postgres` (job queue) |
| `src/doc_ray/application/` | Use cases: `pipeline`, `reannotate`, `reextract`, `jobs` (dispatch + pipeline chaining), `term_scan`/`canonicalize`/`resolution`/`seed_suggest`/`canonical_seed` (entity canonicalization), `semantic_enrichment`, `evaluation`, `backup` (corpus snapshot/restore), `corpus_health`, `db_health` (instance health), `db_analyzer` (multi-engine structure + data profiling) |
| `src/doc_ray/entrypoints/` | Driving adapters (`python -m doc_ray.entrypoints.<name>`): `cli`, `api` (GraphQL), `mcp` (Streamable-HTTP MCP server over the API, run via uvicorn), `worker`, `migrate`, `auth_admin` (principal/token admin), `backup_scheduler` (periodic backup enqueue + retention), `export`, `annotate`, `annotate_spacy`, `annotate_semantic`, `evaluate`, `reannotate`, `reextract`, `corpus_health`, `db_health`, `db_analyze` |
| `src/doc_ray/config.py`    | Env-driven configuration                           |
| `config/env.registry.yaml` | Canonical env-var registry (single source of truth); see [`docs/environment-configuration.md`](docs/environment-configuration.md) |
| `webapp/`                  | Flask browser over the annotated corpus (frontend, kept **outside** `src/`) |
| `scripts/`                 | Standalone text tools (`extract_phrases`, `docmodel`, `roundtrip`, `text_decomposer`) — independent of the pipeline; see `scripts/README.md` |
| `docs/`                    | Design notes & specs; see [`docs/architecture.md`](docs/architecture.md) |

## Architecture Decisions

Architectural and operational decisions are recorded, not just narrated, in
[`engineering_decisions.yml`](engineering_decisions.yml) — a machine-checkable log
validated by the SHACL/YAML toolchain vendored under
[`engineering/schema/`](engineering/schema/). Validate and project it with:

```bash
python engineering/schema/scripts/validate_decisions.py engineering_decisions.yml   # fast YAML gate
python engineering/schema/scripts/yml_to_rdf.py engineering_decisions.yml > /tmp/ed.ttl
python engineering/schema/scripts/shacl_validate.py /tmp/ed.ttl                      # full SHACL gate
```

Current decisions (see the record for rationale, claims, and auditable checks):

| ID | Decision |
|----|----------|
| D-001 | Expose the store through a GraphQL (Strawberry) API |
| D-002 | Serve reads through a `ReadRepository` port + async adapter |
| D-003 | Preserve autocommit + statement-level lexeme collision-grow |
| D-004 | Background jobs via a Postgres queue (`FOR UPDATE SKIP LOCKED`) |
| D-005 | Sync worker for blocking work; async only for API reads |
| D-006 | Runtime migration runner closes the init-only gap |
| D-007 | `jobs` table: text+CHECK status, open text kind (migration 019) |
| D-008 | GraphQL safety: depth + complexity limits, keyset pagination |
| D-009 | Pluggable extractor & analyzer registries |
| D-010 | Access control via Postgres RLS; API sets the principal |
| D-011 | Ruff strict lint incl. docstrings, pinned in pyproject |
| D-012 | mypy runs in strict mode |
| D-013 | Test coverage stays at or above 90% |
| D-014 | Gated pre-commit runs the full quality bar |
| D-015 | Every source file stays under 400 net lines of code |
| D-016 | `engineering_decisions.yml` is the ADR system of record |

## Quality gates

Enforced by [`.pre-commit-config.yaml`](.pre-commit-config.yaml) and CI
(`.github/workflows/ci.yml`), configured in [`pyproject.toml`](pyproject.toml):

```bash
pip install -r requirements-dev.txt && pre-commit install
ruff check src scripts webapp                                   # D-011 (lint + docstrings)
mypy src                                                        # D-012 (strict)
pytest --cov=doc_ray --cov-fail-under=90 tests src/doc_ray/tests # D-013 (>=90%)
python scripts/check_file_size.py src/doc_ray webapp scripts    # D-015 (<=400 net LOC)
uv lock --check                                                 # D-024 (uv.lock drift, BL-114)
```

CI and the Docker image install the runtime dependency set from the committed
`uv.lock` (`uv sync --locked`, D-024) — after editing `requirements.txt`, run
`uv lock` and commit the refreshed lockfile alongside it.

### Dockerized test environment

Do not run the test suite against the current/UAT Compose stack. Use the
isolated test project instead:

```bash
scripts/test-in-docker.sh
```

The script uses [`docker-compose.test.yml`](docker-compose.test.yml) with Compose
project `docray-test`, `docray-test-*` containers, `pgdata-test` /
`miniodata-test` volumes, loopback ports `15432`, `19000`, and `19001`, and a
separate `.env.test` file. It resets only the test project volumes before and
after each run. Pass pytest args to narrow a run:

```bash
scripts/test-in-docker.sh tests/test_compose_security.py -q
```

DB-backed integration tests refuse to run against a non-test database that
already contains non-fixture documents. Use the Dockerized test environment for
local validation; set `DOCRAY_TEST_ALLOW_LIVE_DB=1` only for an explicit operator
override.

## Extending

- **New file types**: add an extractor to `src/doc_ray/adapters/extraction.py`
  and register it in `_EXTRACTORS` (e.g. `.docx`, `.html`, `.txt`).
- **Semantic / RAG search**: add `CREATE EXTENSION vector;` plus a
  `document_chunks(document_id, chunk_text, embedding vector(N))` table with an
  HNSW index — no migration off Postgres.
```
