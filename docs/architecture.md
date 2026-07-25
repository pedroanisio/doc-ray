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
| **Domain** | `doc_ray.domain` | `pdf_layout` (layout→Markdown), `sentences` (segmentation), `lexemes` (identity, tokenization, `RuleAnalyzer`, language detection), `ulid` (value object), `provenance` (events), `ner_corrections` (the pure, deterministic reconcile that re-applies human NER fixes onto ML mentions) | stdlib only |
| **Ports** | `doc_ray.ports` | `TextExtractor`, `ObjectStore`, `Repository`, `Annotator`, `ModelOutputVerifier` — `Protocol` contracts | domain |
| **Adapters** | `doc_ray.adapters` | `extraction` (PyMuPDF + opt-in OCR + opt-in verified external math backend + pypdf + txt/html/epub/docx), `persistence` (Postgres/psycopg), `persistence_read` / `persistence_layers_read` / `persistence_canonical_read` (bounded async GraphQL read models), `object_store` (MinIO/boto3), `annotation` / `annotation_spacy`, `export`, `wikidata` / `wikipedia` (canonical-entity KB resolution), `queue_postgres` (`FOR UPDATE SKIP LOCKED` job queue), `ner_corrections_store` (the durable NER-correction log + its re-apply hook) | domain, ports |
| **Application** | `doc_ray.application` | `pipeline` (ingest use case), `reannotate`, `reextract`, `verification` (shared ML/LLM output gate), `jobs` (worker dispatch + payload-carried pipeline-chain automation), `term_scan` / `canonicalize` / `resolution` / `seed_suggest` / `canonical_seed` (entity canonicalization — see below), `semantic_enrichment` (deterministic discourse/temporal/sentiment/embedding writer), `document_stats` (the D-024 read-model rollup refresh shared by count-mutating jobs), `evaluation` (snapshot + drift metrics), `backup` (corpus snapshot & restore), `corpus_health`, `db_health` (instance health), `db_analyzer` (multi-engine structure + data profiling) | ports, adapters, domain |
| **Entrypoints** | `doc_ray.entrypoints` | `cli`; `api` (GraphQL, Strawberry) and `worker` — the async-read / sync-write split every mutation runs through (see below); `migrate`, `export`, `annotate`, `annotate_spacy`, `annotate_semantic`, `evaluate`, `reannotate`, `reextract`; operational CLIs `auth_admin` and `backup_scheduler`; the diagnostics CLIs `corpus_health` / `db_health` / `db_analyze` | application |
| **Config** | `doc_ray.config` | env-driven `Config` (shared kernel) | stdlib |

## Outside the hexagon

- **`webapp/`** — the Flask frontend. Reads Postgres directly; imports **nothing**
  from `doc_ray`. Every write (upload, domain-seeding decisions, backups) goes
  through the GraphQL API instead — one write path, never a direct queue/DB
  write from the browser tier. Intentionally kept outside `src/`. Presentation
  concerns live only here: the corpus page's view modes (table/compact/gallery/
  grouped) plus sort, density, and grouping are URL-driven layout over the same
  read model, with `sort` whitelisted to a fixed ORDER-BY fragment so a request
  value never reaches SQL (BL-143). The GraphQL API and MCP expose the underlying
  document fields; the layouts themselves are a browser-tier concern.
- **`scripts/`** — standalone CLI tools (`extract_phrases`, `docmodel`, …),
  independent of the pipeline.

## API, worker, and the job queue

`entrypoints.api` is a Strawberry GraphQL app: reads resolve directly against
an async read adapter with bounded keyset pagination (`after` cursors, never
unbounded offsets); every write **enqueues a job** onto a Postgres-backed queue
(`adapters.queue_postgres`, `FOR UPDATE SKIP LOCKED`, heartbeat +
exponential-backoff retry) and returns that job's status — never a synchronous
result. `entrypoints.worker` is the sync process that claims and dispatches jobs
(`application.jobs.run_once`, kind → handler); it runs blocking work (spaCy, KB
network calls, the Anthropic frontier resolver, the backup/restore engine) off
the API's event loop.

A job's payload can carry a `chain`: on success, `run_once` enqueues the next
kind for the same document with the remaining chain before returning —
deterministic and auditable in the `jobs` table, and the mechanism behind
`ingestDocument(pipeline: true)` reaching a fully annotated, semantically
enriched, and canonicalized document (`annotate` -> `semantic_enrich` ->
`canonicalize`) with no further calls.

The API also exposes `GET /jobs/events` as a Server-Sent Events stream for
pipeline status changes. Query with `document_id=<uuid>` to receive only jobs
visible for one document, `job_id=<ulid>` to follow one job, or neither for the
same recent-job scope as `jobs(limit:)`; `once=true` returns a finite initial
snapshot for diagnostics. The stream authenticates with the same bearer or
`X-DocRay-API-Token` headers as GraphQL, binds the Postgres connection to the
caller's principal, and re-reads every notified job through RLS before emitting
it. The queue remains the source of truth: `queue_postgres` emits Postgres
`NOTIFY docray_job_events` payloads from job status transitions, while the
stream only uses those payloads as invalidation hints. The nginx front door has
a dedicated `/jobs/events` location to the API with `proxy_buffering off`, so
EventSource delivery is not delayed by response buffering.

## Stripe billing and payment control plane

Stripe billing is a tenant-scoped control plane around hosted Stripe products,
not an in-app card processor. The React admin console calls the GraphQL
mutations `createBillingCheckoutSession` and `createBillingPortalSession`; the
API creates hosted Stripe Checkout and Customer Portal sessions, then stores only
Stripe identifiers and webhook-derived subscription state. The console reads
`billingStatus` for readiness/entitlement and `billingEvents` for payment
activity, invoice links, and retry state. Unprocessed tenant-visible ledger rows
can be retried through `retryBillingEvent`.

The public payment callback is the FastAPI route `POST /stripe/webhook`, routed
by nginx to the API. It verifies the Stripe signature with
`DOCRAY_STRIPE_WEBHOOK_SECRET`, rejects test/live mode mismatches against
`DOCRAY_STRIPE_SECRET_KEY`, inserts each event into `stripe_webhook_events`, and
updates `tenant_billing_accounts` plus `tenant_subscriptions`. The required
Stripe webhook events are `checkout.session.completed`,
`checkout.session.expired`, `customer.subscription.created`,
`customer.subscription.updated`, `customer.subscription.deleted`,
`invoice.paid`, `invoice.payment_succeeded`, `invoice.payment_failed`, and
`invoice.finalization_failed`.

Paid SaaS writes fail closed when `DOCRAY_BILLING_ENFORCEMENT_ENABLED=true` and
the tenant does not have an `active` or `trialing` subscription for
`DOCRAY_STRIPE_PRICE_ID`. Billing mutations are explicit exemptions so a blocked
tenant can start Checkout, open the Customer Portal, or retry a pending webhook
event, but hosted Checkout and Customer Portal are refused until signed webhook
processing is configured with `DOCRAY_STRIPE_WEBHOOK_SECRET` and a valid
`DOCRAY_PUBLIC_BASE_URL`. Hosted-session return URLs are same-origin checked
against the configured admin-console origin, while `DOCRAY_PUBLIC_BASE_URL`
remains the public webhook origin. In production the admin console is a
loopback-bound sidecar reached via a protected HTTPS ingress such as
`admin.doc-ray.com`; Stripe posts webhooks to the public app origin, not to the
admin console.

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

The GraphQL surface exposes this as read-side contract, not as direct database
mutation: `canonical`, `canonicalSearch`, `unresolvedMentions`, and
`resolutionReview` give operators the registry and review queues; document-level
fields expose annotation runs, tokens with features, dependencies, chunks,
coreference chains, and entity links from the persisted NLP layers.

## Lens registry (entity-resolution scopes)

Today entity identity is **global** — one `canonical` registry for the tenant.
That is correct for real-world entities but wrong for fiction, where the *Duncan
Idaho* of one novel must not collapse into an unrelated book's Duncan. The fix
(the Substrate·Lens·Projection architecture,
[`proposals/entity-fabric-substrate-lens-projection.md`](proposals/entity-fabric-substrate-lens-projection.md))
is to make identity **relative to a named scope — a lens** — rather than to
fragment the registry.

A **lens is a promoted collection**, not a parallel structure:
`document_collections.is_scope` marks one as a scope, `parent_lens_id` optionally
nests it, and `documents.default_lens_id` names a document's default. A reserved
**`global`** lens (undeletable/unrenamable, enforced by trigger) is the
real-world scope. Integrity — a parent lens is a scope, a default lens is a scope
the document belongs to, no parent cycle — is enforced in the database
(migration 121), and the `global` lens's completeness check runs BYPASSRLS as
`docray_batch` (migration 114's pattern). **Block B ships this vocabulary only:
no entity resolution changes; a later projection layer consumes it.** The GraphQL
surface is `lenses` (read) and `promoteCollectionToLens` / `demoteLens` /
`setDocumentDefaultLens` (write); MCP mirrors it with `list_lenses` and the three
write tools.

## Access-control boundary

The API authenticates inbound bearer/API-token requests with
`HeaderTokenAuthenticator`, then drops every HTTP request to `docray_api`, a
`NOLOGIN` member of `docray_client`. Authenticated requests set
`app.principal_id` to the mapped principal; anonymous requests, when enabled for
local/single-operator use, still run as `docray_api` with no principal id and do
not inherit the superuser bypass. Hostile direct-SQL access is bound to
per-principal `LOGIN` roles created by `docray_create_principal_login()` and
stored on `principals.db_role`; those roles inherit the RLS-limited
`docray_client` capability but cannot impersonate another principal by setting
`app.principal_id`. Every RLS-enabled table is `FORCE ROW LEVEL SECURITY`
(migration 106, D-028), so the owning `docray` role is subject to its own
policies and a code path that forgets to `SET ROLE` fails closed instead of
reading the whole corpus. The trusted batch writer — the worker and CLIs, which
annotate across every tenant with no request principal — holds its exemption as
an explicit `docray_batch` role carrying `BYPASSRLS`, assumed by
`adapters/persistence.connect()`. The deployment must connect as a
**non-superuser**: a superuser bypasses RLS regardless of `FORCE`.

A recurring hazard is a table created *after* the `018_rbac` grant loop (and the
`018`/`106` RLS rollout) that is never retrofitted: the batch role reads it, so
ingestion works, while every `docray_client` read path that joins it fails with
`permission denied` (this was `form_index`/`entity_profiles`, migration 117, and
`resolution_review`/`resolutionReview`, migration 123). A per-document table needs
**both** a client grant and an RLS policy — a grant alone would expose it across
tenants. `test_client_read_grants.py` closes the class: it asserts `docray_client`
holds SELECT on every table the client-facing read adapters reference, so the next
such gap fails a test rather than a production caller.

### What a principal may read

The read boundary is document **ownership** (migration 102, D-026).
`docray_readable_documents()` is the union of exactly three branches: documents
in the caller's tenant when the caller is an admin, documents with an explicit
`document_acl` grant to the caller, and documents whose `owner_principal_id` is
the caller. `docray_writable_documents()` carries the same owner branch, so
owning a document implies curating it.

No role confers corpus-wide read on its own. Holding the `read` permission makes
a principal *eligible* to read documents, never *entitled* to all of them — the
distinction migration 076 lost, which let every registered user read the whole
corpus until 102 restored it.

`owner_principal_id IS NULL` means **unowned**, not public: admin-only, produced
deliberately by operator CLI ingest, and reserved as the seam for the future
organization corpus (BL-139). `tenant_id` stays the organization axis and
`owner_principal_id` the user axis, so introducing organizations re-parents
nothing.

Ownership is resolved at the API boundary: `ingestDocument` stamps the request
principal into the job payload, and the worker threads it to `insert_document`.
The worker cannot derive ownership itself — by the time a job runs there is no
request principal to consult.

### What a principal may write

Two independent gates, and both must pass (D-027):

1. **Coarse scope.** `_require_write_audited` refuses any mutation from a token
   without the `write` scope. Scope is *derived* from the principal's RBAC
   permissions — `read` for everyone, plus `write` for anyone holding
   `annotate`, `delete`, `redact`, or `admin` — never from a role-name
   allowlist, so re-granting a role changes what its members may do with no code
   change.
2. **Per document.** `_require_document_write` checks membership of
   `docray_writable_documents()`, so the scope says *whether* a principal may
   mutate and RLS says *what*.

`member` (read + annotate + delete) is the self-service registration role and
what every self-registered human holds. `reader` is genuinely read-only and is
reserved for SCIM/OIDC- and admin-CLI-provisioned accounts. Granting members
`annotate`/`delete` is not an escalation: no read or write policy consults those
permission rows — only `docray_is_admin()` reads the permission tables — so a
member's reach is exactly what they own or were granted.

### Sharing

An owner (or tenant admin) hands a document to a colleague with
`shareDocument(documentId, email, permission)` — `read` or `annotate`, never
`admin`/`redact`/`delete` — and takes it back with `revokeDocumentShare`
(migration 108, D-029). Both write `document_acl`, which is itself
`FORCE ROW LEVEL SECURITY`: its policies scope every grant to a document the
caller owns (`docray_can_share_document`), so the INSERT grant the API role now
holds cannot forge access to another document. `_require_document_share` is the
clean-error layer; the RLS policy is the backstop. Sharing is by login email,
resolved through tenant-scoped `SECURITY DEFINER` helpers so the API role never
reads `principal_credentials` (which holds password hashes) and lookups cannot
cross tenants. The admin CLI's arbitrary-permission `grant_document` is
unaffected because `auth_admin` assumes `docray_batch` (BYPASSRLS), the same
trusted-writer identity the worker uses.

### Organization corpus

Beyond person-to-person sharing, an owner (or tenant admin) publishes a document
to their whole organization by setting `documents.visibility = 'org'`
(migration 110, D-031) — via `setDocumentVisibility` or the Share page's
"Publish to my organization" control. `docray_readable_documents()` gains a
branch admitting any org-visible document in the caller's tenant, so the tenant
boundary is the organization boundary and org visibility never crosses tenants.
Documents default to `private`; this is the safe inverse of the migration-076
leak (default-visible-minus-exceptions), and the org branch is gated on
`visibility = 'org'` rather than the read permission, so it is structurally
incapable of that failure. The column's only write path is
`docray_set_document_visibility`, a `SECURITY DEFINER` function that re-checks
owner-or-admin authority in the database — so `docray_client` needs no `UPDATE`
grant on `documents` and an annotate-grantee cannot publish a document lent to
them.

Note that corpus writes do not need table-level DML grants: a mutation *enqueues*
work that the trusted batch writer executes. The jobs queue is trusted
infrastructure, not a user-writable table. Authorization happens once, at the API
boundary — the mutation guards check write scope, per-document ACL, or admin under
the caller's principal — and the enqueue itself runs as `docray_batch`, elevated
`LOCAL` to the insert. So `jobs` needs no user-facing `INSERT` policy at all: with
`FORCE ROW LEVEL SECURITY` that denies every non-bypass role, and only the trusted
writer enqueues. This is deliberate, not incidental — an upload's ingest job is
document-less at enqueue time (the document does not exist until the worker creates
it), so no `document_id`-keyed policy could ever admit it. RLS on `jobs` therefore
governs **read** visibility only: a caller sees the jobs they enqueued (`enqueued_by`)
or those on a document they can read. Both write gates above live in the mutation
layer for exactly this reason (BL-141).

## Deployment guardrails

Compose binds Postgres to `127.0.0.1` by default, requires a non-default
`POSTGRES_PASSWORD`, and pins the pgvector/Postgres image by digest so routine
pulls do not silently change the collation library behind an existing `pgdata`
volume. API connection-pool bounds are explicit (`DOCRAY_API_POOL_MIN_SIZE`,
`DOCRAY_API_POOL_MAX_SIZE`, `DOCRAY_API_POOL_TIMEOUT`) so deployment capacity is
configured rather than inherited from psycopg defaults.

## Ports & their adapters

| Port | Adapter |
|---|---|
| `TextExtractor` | `adapters.extraction.extract` — dispatch by extension; PDF via PyMuPDF (structured Markdown), opt-in Tesseract OCR for image-only pages, opt-in verified external math extraction for LaTeX-bearing PDFs, and a pypdf fallback |
| `ObjectStore` | `adapters.object_store.ObjectStore` — MinIO/S3 blob store |
| `Repository` | `adapters.persistence` — Postgres data-access (documents, spans, sentences, lexemes, annotations) |
| `Annotator` | `domain.lexemes.RuleAnalyzer` (zero-dep) and `adapters.annotation_spacy.SpacyAnnotator` (real POS) |
| `ModelOutputVerifier` | `application.verification.verify_model_output` + adapter-specific verifiers such as `CandidateResolutionVerifier`; failed ML/LLM output is rejected before mapping or persistence |

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

`backup-scheduler` is the deployed cadence trigger: it periodically enqueues
`backup` jobs through the same Postgres queue and prunes old object-store
archives according to `DOCRAY_BACKUP_RETENTION_*`. This remains snapshot-based
recovery; WAL/PITR archiving is intentionally outside the app-native backup
engine rather than silently implied.

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

## NLP capability matrix & drift guard

Schema breadth is not the same claim as production support: a migration can
define a table years before any writer, read adapter, or GraphQL field touches
it. [`docs/nlp_capability_matrix.md`](nlp_capability_matrix.md) makes
that distinction explicit for all 20 documented NLP layers — schema, writer,
read adapter, GraphQL, browser, health, and tests are tracked as separate
columns, collapsing to one status (`api-exposed` → … → `planned-only`) per
layer. `src/doc_ray/tests/test_nlp_capability_matrix.py` is a static drift
guard over that table: it fails if a documented layer goes missing, if a row
claims GraphQL exposure that doesn't resolve to a live Strawberry field, or if
a row claims a writer without a citable production path and test. It proves
the matrix hasn't drifted from the code; it does not evaluate annotation
quality.

`application.semantic_enrichment` now fills the deterministic subset that does
not require a dedicated model or KB: PDTB-style connective relations, simple
TimeML date/event rows, lexicon sentiment, and hash-BoW span embeddings. These
rows are explicitly heuristic and provenance-stamped; subword tokenization,
constituency, SRL, WSD, and AMR remain schema-only until a real model-backed
writer lands.

Where the matrix answers *which* layers are supported,
[`docs/nlp_architecture.md`](nlp_architecture.md) answers *how* the running
system does NLP: the segmentation and language-detection algorithms, the spaCy
pass and its validation gate, the deterministic-before-statistical-before-
generative posture of the canonicalization flywheel, the verification gate behind
each model output, the three retrieval paths and their differing semantics, and
the known limits of each layer.

### Human corrections to ML entities

Named-entity output is a model's guess, so the corpus carries a verification
layer over it. The constraint that shapes the design: `entity_mention` rows are
`ON DELETE CASCADE` from `annotations` and are rebuilt from scratch by every
annotate run, so a correction stored there would be silently destroyed by the
next pass. Corrections are therefore written to `entity_mention_correction`
(migration 115) keyed by a **stable span identity** — `(document_id, char_start,
char_end)`, the same natural key `spans` already uses — and re-applied onto each
fresh set of mentions by `domain.ner_corrections.reconcile()`, a pure function
with no I/O: deterministic, idempotent, and ordering-independent, so replaying
the log always yields the same mentions.

Ownership of the write follows the role split. `entity_mention` and `spans` are
the annotate pipeline's tables — `docray_api`/`docray_client` hold only `SELECT`
on them — so the GraphQL mutation never edits mentions directly. It records the
correction (RLS-scoped to the document's writable set) and enqueues an
`apply_corrections` job; the batch worker, which does own those tables, runs the
reconcile. The same reconcile runs unconditionally at the tail of `handle_annotate`,
which is what makes a fix survive re-annotation rather than merely precede it.

`application.evaluation` records durable corpus snapshots and evaluation runs.
It stores lexical/entity distributions and review/tool-version facts in
`corpus_snapshot`, writes citeable `evaluation_metric` rows, and compares
snapshots deterministically so drift can be audited across pipeline versions.

## Running

```bash
# tests use pyproject's pythonpath (src, ., scripts) — no install needed
python -m pytest tests/                 # session suite
python -m pytest src/doc_ray/tests/     # NLP-layer suite

# CLI (host): src/ must be importable
PYTHONPATH=src python -m doc_ray.entrypoints.cli <file-or-dir>
# in Docker the image sets PYTHONPATH=/app/src
```
