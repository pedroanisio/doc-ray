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

## [1.46.0]

### Added

- **Projection Engine — lens-scoped entity identity without lossy merges (SLP Block D,
  issue #8).** Entity identity is no longer a fact baked into the substrate; it is a
  **derived, refreshable interpretation**: `entity = project(mention, lens)`. The same
  corpus can now hold fiction and non-fiction side by side — a fictional *Duncan* stays
  inside its work lens while the real *Paris* is shared across every history — with no
  lossy merge, because a projection is derived (drop and rebuild) where a merge is
  destructive. The pure resolver (`domain.projection.project`) encodes the law —
  *policy decides local vs global → local-first lookup → global fallback → explicit
  unresolved* — consuming the Block C genre policy (#6) and the Block B lenses (#5).
  - **Schema (migration 129, additive; RLS + FORCE on every table):**
    `entity_projection` (one row per mention × projecting lens; `scope` local/global,
    a nullable `canonical_id` where NULL = explicitly unresolved, and a
    `proposed`/`confirmed`/`rejected` review status — batch-owned, SELECT-only to the
    API); `lens_local_entity` (the stable registry that mints one `canonical` row per
    (lens, surface, type) so a rebuild is byte-identical); `entity_projection_review`
    (human confirm/reject keyed by the **stable span**, so a decision survives
    re-annotation — Block A's record-then-apply shape); `lens_entity_stats` (the
    per-lens entity rollup `lensEntities` reads, D-024); and
    `document_collections.projects` — only a **designated** lens is materialized
    (FR-2 cost control, bounded per document).
  - **Refresh:** `KIND_REFRESH_PROJECTION` / `handle_refresh_projection` — incremental
    per document (chained after canonicalize / reannotate / apply_corrections, so a
    projection is never stale) and a full operator rebuild. Reproducible from
    substrate + lens + policy; the substrate is never mutated (invariants 1–2).
  - **Surfaces:** GraphQL `entityProjection` / `lensEntities` (reads),
    `confirmProjection` / `rejectProjection` / `setLensProjects` (writes, guarded +
    audited); MCP `entity_projection` / `lens_entities` (reads) and the write-gated
    `confirm_projection` / `reject_projection`. Absent a `lensId`, a read uses the
    document's default lens, else `global` — every existing caller is unchanged.
    Schema SemVer → **1.46.0** (migration 130). See `docs/architecture.md` and
    `docs/mcp-service.md`.

## [1.45.0]

### Added

- **Genre / type classification + per-entity-type resolution policy (SLP Block C,
  issue #6).** Every document can now carry a **genre axis** — fiction /
  non_fiction / mixed / unknown — orthogonal to its file format (`doc_type`), in a
  new `document_classification` table (migration 127, RLS + FORCE). A heuristic
  classifier (`KIND_CLASSIFY_GENRE` job, deterministic signals — dialogue density,
  first-person narration, chapter headings vs. an academic apparatus, and metadata;
  no LLM) writes a **pending** guess; a human confirms it (`method='human'`,
  `status='confirmed'`) — a machine guess is never asserted (PALS's Law). A pure
  policy function `domain.genre_policy.resolution_scope(genre, entity_type)` answers
  whether an entity resolves **LOCAL** (a fiction work's own lens) or **GLOBAL**
  (shared identity), with a conservative GLOBAL default for `mixed`/`unknown` and
  for real-world types (DATE, MONEY, LAW, …) even inside fiction.
  - **This block only *defines* the policy — nothing resolves differently yet.**
    Block D (#8, the projection engine) consumes it.
  - Surfaces: GraphQL `documentClassification` (read), `classifyDocument` +
    `confirmDocumentClassification` (writes, guarded + audited); MCP
    `document_classification` (read) and the write-gated `classify_document` /
    `confirm_document_classification`. Schema SemVer → **1.45.0** (additive table,
    migration 128). See `docs/architecture.md` and `docs/mcp-service.md`.

## [1.44.0]

### Added
- **Semantic retrieval for `briefingPack` — rank passages by meaning, not keywords**
  (Claude Opus 4.8 (1M context) via Claude Code; issue #33, under epic #23). The
  shared `application.briefing` service now ranks each matched document's passages by
  **learned semantic similarity** (the same sentence encoder behind
  `semanticSearchSentences`), RLS-scoped, so a paraphrase surfaces even with no shared
  words. The ranking lives once in the shared service — MCP `briefing_pack`, GraphQL
  `briefingPack`, and the `/briefing` webapp add none — so the three surfaces stay
  **byte-equivalent** for the same visible slice. No schema change; no version bump.
  - **Deterministic, reported fallback** (never silent): when the sentence stack is
    not installed (`SemanticUnavailable`) or a document has no visible sentence
    vectors, it ranks by full-text/keyword instead and states which path ran in
    `retrieval` — and in `gaps` when it fell back. Document discovery remains
    full-text. Cite handles, ordinals, coverage, entity evidence, passage budgets, and
    gap reporting are unchanged.
  - **Concurrency fix (latent):** the GraphQL reader serves all reads on one
    request-scoped connection, which cannot run the service's `asyncio.gather`ed reads
    concurrently; a per-reader lock now serializes that connection (the MCP reader is
    unaffected — it opens a client per call). This surfaced only once a real
    connection drove the briefing under load.
  - New: `BriefingReader.semantic_search_sentences` + `SemanticUnavailable` in
    `application/briefing.py`; reader wiring in `entrypoints/mcp/compose.py` and
    `entrypoints/api/briefing_query.py`. Docs: `docs/mcp-service.md`, `docs/architecture.md`.

- **Pipeline cost telemetry (migration 126):** append-only per-job execution
  telemetry for cost attribution, with a GPU/CPU lane mapping so a GPU host-hour
  and a CPU host-hour are priced apart, rate configuration in
  `config/cost_rates.yaml`, and a `scripts/cost_telemetry.py` reporter. See
  `docs/pipeline-cost-telemetry.md`.

### Fixed

- **Admin Console operator-health panel: correct diagnosis + contract (issue #22).**
  The panel's `Unexpected token '<', "<!doctype "…` error was a `JSON.parse`
  failure — nginx's SPA fallback served `index.html` (HTTP 200 + HTML) for a
  request that was not reverse-proxied to the webapp — but the UI blamed a missing
  `DOCRAY_OPERATOR_HEALTH_TOKEN` for **every** failure. Fixes: (1) `client.rest()`
  now guards the response content-type and raises an actionable, routing-pointed
  `ApiError` instead of a cryptic `SyntaxError`; (2) the `OperatorHealth` type and
  the dashboard now follow the `operator-health.v1` contract — corpus metrics under
  `corpus.totals`, the job spool under `corpus.jobs`, and issues (with `detail`, not
  `message`) from both `corpus.issues` and `database.issues` — so the panel renders
  real data instead of blanks; (3) errors are reported by cause (404 → token/session,
  content-type → routing, 5xx → webapp fault), dropping the blanket token suffix;
  (4) new regression guards tie the TS `OperatorHealth` type to
  `webapp/health_contract.py` and assert the nginx `location /operator/` block
  proxies to `WEBAPP_UPSTREAM`. The deployment-side routing fix (defect 1) is
  environment work — the guard prevents the template from silently losing the block.

- **Migration runner survives a stop mid-migration (issue #4).** A routine
  `docker compose down`/stop while a long backfill (e.g.
  `040_chunk_surface_backfill.sql`) was running used to escalate SIGTERM to SIGKILL
  — the alarming triple `exit 137`, and a risk of a future non-restart-safe
  migration being hard-killed mid-statement. Now the `migrate` entrypoint installs
  SIGTERM/SIGINT handlers that **cancel the in-flight statement through the live
  connection** and exit promptly with `128 + signal` (130/143); the transaction
  rolls back and the idempotent, self-recording migration resumes on the next
  start. `postgres` and `migrate` also declare an explicit `stop_grace_period`
  (300s / 120s) so Postgres can finish its shutdown checkpoint and the runner can
  unwind before Docker escalates to SIGKILL. New:
  `apply_migrations(..., on_connection=...)` hook in `adapters/migrate.py`; exit
  codes documented in `docs/public/reference/cli.md`. No schema change; no version
  bump.

- **Test-suite root causes (47 failures):** shared-queue isolation, stale SQL
  stubs that silently returned `{}`, D-024 rollups left unpopulated by test
  seeding, a `ReadRepository` port that had drifted from its adapter, guards
  pinning behaviour the product deliberately changed, and operator documentation
  lost from the generated env samples.

## [1.43.0]

### Added

- **Webapp experience epic (#23):** shipped learned semantic search and corpus
  backfill operations, metadata/entity and visual-caption review lanes, reusable
  lenses, byte-stable cited briefing packs, document image delivery and gallery
  views, and cross-modal image discovery.
- Exposed the new capability consistently through GraphQL, the Python-facing
  application/repository boundaries, MCP read/write tools, and the Flask webapp.
  A strict parity checker now compares the GraphQL schema with documented and MCP
  surfaces so a future field cannot silently ship on only one interface.
- Added operational runbooks, public how-to guides, architecture/reference
  updates, release notes, and a schema marker migration for the deployable
  `1.43.0` release.

### Changed

- Learned sentence embedding is routed to the long-running worker lane and the
  corpus backfill path reports evidence-ready coverage without reintroducing the
  discarded hash-BoW model.
- Tier-2 visual embedding now streams each Document through bounded,
  order-preserving image and text batches (default `16`, configurable with
  `DOCRAY_VISION_BATCH_SIZE`) instead of allocating an entire large Document on
  the GPU at once. Invalid or non-positive configuration fails before bytes are
  loaded, and `worker-fast` excludes `visual_embed` so GPU-only work cannot be
  claimed by the reserved CPU lane. The heavy lane gives this job a configurable
  `WORKER_JOB_TIMEOUT_VISUAL_EMBED_SECONDS` (14,400-second default).
- Model-generated Tier-3 captions remain untrusted until an attributable human
  approves them; all review reads remain RLS-scoped and all decisions are audited.

## [1.42.0]

> ARCHITECTURAL REQUIREMENT (PALS's LAW): LLMs/ML models always produce some form
> of error. The sentence encoder's output is used for **ranking only** and asserts
> no fact into the trusted graph, so it needs no verification gate — it is safe by
> *use*, not by verification (mirrors the Prism vision encoder).

### Changed
- **In-app corpus backup is now a feature toggle, off by default** (Claude Opus 5
  (1M context) via Claude Code; D-033). `DOCRAY_BACKUP_ENABLED` gates the whole
  subsystem — the `backup-scheduler` service, the `backup`/`restore` job kinds,
  the archive download route, and the backup UI in both the webapp and the admin
  console. It defaults to `false`; production runs with it off and relies on the
  managed database layer's automated backups and PITR.
  - **Enforced at every entry point**, each reading the new
    `domain.jobs.BACKUP_JOB_KINDS` rather than naming the kinds again: the
    scheduler exits `0` without enqueueing or pruning; `createBackup` /
    `restoreBackup` refuse *before* the admin check, so a disabled deployment
    answers "the feature is off" instead of the misleading "you are not an
    admin"; `/backups/<id>/download` 404s before touching auth or the database;
    the webapp's four backup routes 404 and their nav entry is hidden.
  - **Queued rows drain, they do not pile up.** A worker that claims a `backup`
    or `restore` row while the flag is off *cancels* it with a recorded reason
    rather than leaving it queued for a lane that will never take it.
  - **New `backupEnabled` query field** so a client can check the capability
    before offering the action; the admin console uses it to hide its nav entry
    and to explain itself on a deep link.
  - **Why:** the subsystem had never once succeeded in production — 27 failed
    rows, 2 stuck, zero `available`, and an empty `backups/` prefix in the
    bucket. It failed two ways: `EntityTooLarge` (the archive is built wholly in
    memory and uploaded with a single `PutObject`, past S3's 5 GiB single-PUT
    ceiling) and `JobExecutionTimeout` at 3600s. Worse, `backup` was claimable by
    the only worker pool that runs `annotate`, so each attempt held that slot for
    an hour: on 2026-07-25 a user-facing annotate job queued 39 minutes behind
    one.
  - **Known gap, accepted (D-033):** managed-database backups cover the database
    only. This archive was the sole mechanism capturing object-store blobs, and
    the production bucket has no versioning — with the flag off, original
    document bytes have a single copy and no point-in-time recovery. Closing it
    needs bucket versioning or a separate blob backup.
  - No schema change; no version bump. `make prod-up` and the other `up` targets
    no longer start `backup-scheduler`.

### Added
- **MCP Ops Phase 3 — health, readiness, rate limiting, and the token runbook**
  (Claude Opus 4.8 (1M context) via Claude Code; issue #18). The MCP service — live
  in production ahead of its ops surface — gains the operational surface every other
  service in the stack already has. No schema change; no version bump.
  - **`GET /health`** (liveness): `200 {"status":"ok","version":…}` whenever the
    process runs, **dependency-free** — a regression test fails if it ever acquires a
    network call, because a liveness probe that checks the upstream makes the
    orchestrator kill a healthy process. **`GET /readyz`** (readiness): `200` only when
    the config loaded and the GraphQL upstream answers, else `503` naming the failed
    check; the upstream probe is a ≤2 s GET and a negative result is cached ~5 s so a
    readiness storm cannot amplify an outage. Both are unauthenticated, information-free
    (no host/URL/stack trace), and bypass `BearerAuthMiddleware` and the limiter via
    `health.PUBLIC_PATHS`. `entrypoints/mcp/health.py`, mirroring the API's `/health`+`/readyz`.
  - **Per-principal rate limiting** (`entrypoints/mcp/ratelimit.py`): `429` + `Retry-After`
    on breach, keyed by a non-reversible token fingerprint (IP fallback), logged on
    rejection by fingerprint — never the token. Configurable via
    `DOCRAY_MCP_RATE_LIMIT_REQUESTS` (default `120`) / `DOCRAY_MCP_RATE_LIMIT_WINDOW_SECONDS`
    (default `60`); `0` disables. State is in-process (per container; N replicas ⇒ N×,
    documented). **No second limiter**: the pure fixed-window core moved to
    `doc_ray.domain.rate_limit`, shared with the webapp (`webapp/rate_limit.py` is now a
    re-export shim — no behaviour change).
  - **Compose healthcheck** now hits `/health` (was a bare TCP-port check), so
    `depends_on: condition: service_healthy` is meaningful. **Token runbook** added to
    `docs/production-setup.md`: mint (`auth_admin token mint --principal … --scopes read`),
    why `write` is not the default, static-token vs OAuth DCR, rotate, revoke, and reading
    a principal's audit trail. Ops surface documented in `docs/mcp-service.md` §3.8; §4
    Phase 3 fully ticked.
  - **Migration guidance:** none required — rate limiting ships **enabled** with a
    generous default that only catches runaway loops. To restore the previous unlimited
    behaviour set `DOCRAY_MCP_RATE_LIMIT_REQUESTS=0`. New env keys are additive and
    registered in `config/env.registry.yaml`.
- **MCP prompts primitive — ship the corpus methodology as selectable workflows**
  (Claude Opus 4.8 (1M context) via Claude Code; issue #13, MCP Item 8). The server
  now declares MCP's third and final primitive — **prompts** (user-controlled, joining
  the model-controlled tools and application-controlled `docray://` resources) — as
  four argument-taking workflows a user picks from their client's menu, so the
  retrieval methodology lives in the server instead of being re-derived per session.
  No schema change; no version bump.
  - **`summarize_document(document_id)`** is **eager** (the primitive's justification):
    it fetches the document's real title, sentence count, and outline, then returns a
    plan already naming the ordinal ranges to read with `response_format="concise"` and
    the rule that every claim cites an `ordinal`. **Honesty propagates**: a `uniform`
    outline is disclosed as a navigation grid whose titles are **not** chapter names,
    never laundered into a table of contents.
  - **`ground_a_claim(claim, document_id?)`** — the search-then-verify path, with the
    non-negotiable rule that an ungrounded claim is reported *unsupported*, never
    asserted. **`survey_entities(document_id?)`** — `entity_profiles` over
    `entity_frequency`, how to read `typeConsistency`, corroborate the contested.
    **`sweep_the_corpus(shard_count=8)`** — the map-reduce plan with the coverage proof
    (Σ per-shard counts == `totalDocuments`).
  - **Failure is graceful** (FR-4/FR-5): a malformed id is rejected (`bad_input`) before
    any network call; an unreadable id returns a plain "not readable — use
    `list_documents`" message with no fabricated plan; and if the outline can't be
    fetched the plan degrades to a self-serve one and says so. Each prompt emits **one
    audit line** per invocation (the same `audit.audited` wrapper as tools, verified to
    compose with `FastMCP.prompt()`), returns a single user-role message, holds no DB
    pool, and never embeds the caller's token in its text. Each message is well under
    ~1.5 KB. New: `entrypoints/mcp/prompts.py`; documented in `docs/mcp-service.md` §3.7.
- **Term-frequency pre-flight — prevent search flooding instead of diagnosing it**
  (Claude Opus 4.8 (1M context) via Claude Code; issue #16, MCP Item 10). A new
  read — GraphQL `termFrequency(terms:)` and MCP tool `term_frequency` — lets an
  agent ask how common a **batch** of candidate search terms is *before* spending a
  search call, and get a categorical `floodRisk` (`low` | `moderate` | `high`) so it
  picks a rarer term for free. BL-145 shipped the *diagnosis* (a flooded
  `search_sentences` returns `coverage.limit_reached` + a note); this removes the
  wasted call the diagnosis still costs. No schema change; no migration; no version
  bump.
  - **One indexed statement over the D-024 rollup:** the whole batch is served by a
    single `unnest`/`LEFT JOIN`/`SUM` against the `lexeme_stats` rollup — never the
    17.5M-row `lexeme_occurrences` fact table (the measured 37 s Lexicon
    regression). RLS-scoped to the caller's tenant; a regression test fails if the
    query ever references `lexeme_occurrences` again.
  - **The verdict is the product (FR-2):** `floodRisk` is the term's `share` of the
    scope's total lexeme uses, bucketed at `share ≥ 0.5%` (high) / `≥ 0.05%`
    (moderate); the payload carries `uses`/`totalUses`/`share` so a caller can
    disagree, and `attested: false` + an "unattested" note distinguishes an unknown
    term from a lookup failure (FR-6). Verdict logic is a pure domain module,
    unit-tested at every threshold boundary.
  - **Normalization (FR-5), answered empirically and documented:** the count is the
    lexeme-lemma frequency (`norm_lemma`), a corpus-saturation proxy that is
    deliberately **not** identical to what `search_sentences` (case-insensitive
    substring) or `full_text_search` (Snowball stem) match — recorded as a finding
    in `docs/mcp-service.md` §3.6 rather than papered over.
  - **Corpus-wide only for now** (Open decision 1a): passing `documentId` /
    `document_id` returns a typed `unsupported` error rather than corpus numbers
    mislabelled as document-scoped; a `document_lexeme_stats` rollup is the
    follow-up if the evaluation harness shows document-scoped triage matters.
  - **Surfaces:** the server instructions teach the pre-flight step; the tool is
    registered in every MCP drift guard; and it is exercised by a new
    `coverage-term-frequency` task in the evaluation harness (#12). New:
    `domain/term_frequency.py`, `adapters/persistence_read_terms.py`.
- **MCP evaluation harness — measure the tool surface instead of asserting it**
  (Claude Opus 4.8 (1M context) via Claude Code; issue #12, MCP Item 11). A
  reproducible, offline harness runs a fixed set of grounded retrieval tasks
  against a **seeded, disposable** fixture corpus and reports task success,
  tool-call count, and payload size, so a change to a tool description or return
  shape can be *shown* to help or hurt rather than merely asserted to — the
  programme-level application of PALS's Law (the outputs being consumed are model
  behaviours, unverified by default). No schema change; no version bump.
  - **Scripted mode** (default, deterministic, CI-safe) executes a fixed call
    sequence per task and measures the *surface*: it calls the real tool
    callables (`corpus`/`semantic`/`structure`/`compose`) and routes their GraphQL
    over an **in-process** Strawberry schema (the harness holds no API server), so
    the real `corpus.gql` error-mapping path — including the cursor-steering hint —
    is exercised. **Agentic mode** ships as a mode switch that fails with a typed
    `EvalModeUnsupported` (no model credentials are provisioned here); it is never
    a silent no-op.
  - **Six grounded tasks** (FR-2), each derived from a failure this programme
    observed — exhaustive coverage, rare-term localisation, surviving a keyword
    flood, reading the ending, the type-split entity roster, and the cursor
    dead-end — plus coverage tasks that exercise every *other* registered read
    tool (including `semantic_search_sentences`, whose documented `unsupported`
    outcome counts as reachable). A drift guard (`test_mcp_eval_tasks.py`) fails
    the build if any registered read tool is exercised by no task.
  - **Baseline + comparison:** a committed baseline at `docs/eval/mcp-baseline.json`
    (produced by an actual run); `scripts/eval_mcp.py --baseline <path>` exits
    non-zero on a regression beyond tolerance. Token counts are a documented
    estimate (`chars ÷ 4`), never reported as a measurement.
  - **New:** `scripts/eval_mcp.py`, `application/mcp_eval.py`,
    `application/mcp_eval_tasks.py`, `application/mcp_eval_fixtures.py`,
    `domain/mcp_eval_scoring.py`, and `docs/mcp-evaluation.md` (usage, how to read a
    report, how to add a task). CI gating is **not** enabled initially (operator's
    call once the numbers are trusted).

### Changed
- **NLP Layer 20 — hard cutover from `hash-bow-768` to a learned sentence encoder**
  (Claude Opus 4.8 (1M context) via Claude Code; issues #20, #21). **BREAKING** for
  the embedding vector space. The old sentence embedding was `hash-bow-768` — a
  deterministic BLAKE2b bag-of-words hash where distinct words landed in unrelated
  buckets, so it carried no semantics and (verified) *no reader ever queried it*.
  Strategy: **hard cutover / replacement** — old and new never serve production
  traffic together, and the hash-BoW implementation is **discarded, not kept as a
  fallback**.
  - **Writer replaced.** `application/semantic_enrichment.py` no longer writes
    embeddings (its `_vector_for` hash, `EMBEDDING_MODEL`, and the embedding
    COPY/DELETE are removed). A new learned writer — `application/semantic_embed.py`
    behind the `TextEncoder` port (`ports/text_encoder.py`) and its
    sentence-transformers adapter — is the sole writer, run by the new opt-in,
    never-auto-chained `semantic_embed` worker job (default model
    `sentence-transformers/all-mpnet-base-v2`, tag `doc-ray/sbert-mpnet-768`,
    768-dim). Heavy stack lives in the worker only; absent-stack fails
    typed-and-actionable, never a raw `ModuleNotFoundError`.
  - **Retrieval unlocked.** New `semanticSearchSentences` GraphQL field + MCP
    `semantic_search_sentences` read tool rank sentences by learned cosine
    similarity over the `embedding` table (RLS-scoped via the existing
    span→document policy; HNSW iterative scan for correct top-N under RLS). This is
    the antidote to lexical-search flooding on common terms.
  - **Cutover.** Migration 124 purges the discarded `hash-bow-768` rows and bumps
    the schema version. Re-embed the whole corpus in one shot with the admin
    mutation `embedCorpusSentences(onlyMissing: true)` (re-runnable; skips
    already-embedded documents), or per document via `embedDocumentSentences`.
    Take a corpus backup first — that snapshot is the rollback path.
  - **Availability.** The learned encoder (`sentence-transformers`) is a **base
    dependency**, baked into the worker image next to the Prism `open_clip_torch`
    encoder — the production `Dockerfile` (`uv sync --locked`) never installs
    optional extras, so shipping it only as `.[semantic]` left every `semantic_embed`
    job failing with `SemanticStackUnavailableError`. The `.[semantic]` extra is
    retained as a convenience alias. The heavy tensor stack (`torch`,
    `transformers`) was already base (pulled by `spacy[transformers]`), so this
    adds only the thin encoder layer. New env `DOCRAY_SEMANTIC_MODEL`.
  - **Device posture — GPU when possible, CPU otherwise.** `semantic_embed` is now
    excluded from the `worker-fast` pool (like `annotate`), so heavy model-loading
    jobs never block light user jobs. The heavy `worker` pool is GPU-accelerated by
    environment assessment: `scripts/deploy_env.py` probes whether a GPU is usable in
    a container and `make up` layers `docker-compose.gpu.yml` accordingly (one GPU
    worker on an 8 GB card — two OOM — with the CPU `worker-fast` pool alongside),
    falling back to CPU on hosts without one. sentence-transformers auto-selects CUDA
    there; new env `DOCRAY_SEMANTIC_DEVICE=auto|cpu|cuda|cuda:N|mps` forces a device.

## [1.41.0]

### Fixed
- **`resolutionReview` was dead for every client caller — `resolution_review`
  never granted to `docray_client`, and never RLS-scoped** (Claude Opus 4.8 (1M
  context) via Claude Code; issue #15, broader sweep). Migration 117 fixed the
  `form_index`/`entity_profiles` instance of this class; the class-closing sweep
  the issue asked for surfaced a second: `resolution_review` (read by the
  `resolutionReview` GraphQL query via `list_resolution_review`) was created in
  migration 023 — *after* both the 018 grant loop and the 018/106 RLS rollout — so
  it had **neither** a `docray_client` SELECT grant **nor** RLS, and every real
  caller got `permission denied for table resolution_review`. Unlike the global
  `form_index`, `resolution_review` is per-document (via
  `mention_id → entity_mention → annotations.document_id`), so migration 123 adds
  **RLS scoping** (a bare grant would expose every tenant's review queue) *and* the
  grant. A new `test_client_read_grants.py` closes the class: it asserts
  `docray_client` holds SELECT on every table the client-facing read adapters
  touch, so the next table added after a grant loop cannot silently repeat this.
  No schema-structure change (grant + RLS only); `docray_schema_version()`
  untouched, as with 117.
- **`entity_dossier` reported a capped page as the corpus total** (Claude Opus 4.8
  (1M context) via Claude Code; issue #14). The MCP tool derived `total_mentions`,
  its `documents` roster, and `gaps` from a single `search_sentences` page capped
  at `max_mentions`, so it claimed `total_mentions == max_mentions` and a
  single-document roster with `gaps: []` for an entity that was demonstrably in
  more documents — a silent false negative dressed as a complete answer, the exact
  failure mode the project's verification stance exists to prevent, and one its own
  `guidance` string actively encouraged. The fix draws the document roster from
  `full_text_search` (the complete set of documents containing the entity,
  independent of the mention cap), keeps the `coverage` signal `search_sentences`
  already returns, and reports honestly: `total_mentions` becomes `cited_mentions`
  (the passages shown, never a total), a new `document_count` and `truncated` flag,
  a `cited_mention_count` per roster document (0 where the entity appears but no
  passage was cited), and `gaps` + `guidance` that name the truncation instead of
  asserting completeness. No schema change (MCP-layer logic only). The pure
  citation/response-budget helpers were extracted to
  `entrypoints/mcp/compose_common.py` (D-015).

### Added
- **Lens Registry — promote collections to named entity-resolution scopes**
  (Claude Opus 4.8 (1M context) via Claude Code; Block B, issue #5). A *lens* is a
  `document_collections` row flagged `is_scope`: a named scope under which a later
  projection layer (Block D) will interpret entity identity, so the fictional
  *Duncan Idaho* of one work stays distinct from an unrelated novel's Duncan while
  the reserved **`global`** lens holds real-world (Wikidata-linkable) identities
  shared corpus-wide. This block ships **vocabulary and metadata only** — it does
  not change how any entity resolves today; every existing read returns
  byte-identical results (proposal
  `docs/proposals/entity-fabric-substrate-lens-projection.md`, §3.2/§7).
  - **Schema (migration 121):** `document_collections.is_scope` and
    `parent_lens_id`; `documents.default_lens_id`; the reserved `global` lens
    seeded idempotently. Integrity is enforced in the database, not only the app:
    a parent lens must itself be a scope, a document's default lens must be a
    scope it is a member of, the parent graph may not cycle, and `global` cannot
    be renamed, demoted, or deleted — all by trigger. Deleting a lens `SET NULL`s
    the references that point at it; unmembering a document from its default lens
    clears the default.
  - **GraphQL:** `DocumentCollection.isScope`/`parentLensId`, `Document.defaultLensId`,
    a `lenses` query, and the `promoteCollectionToLens` / `demoteLens` /
    `setDocumentDefaultLens` mutations (write-scope + per-document-ACL guarded,
    audited on success and denial).
  - **MCP:** the `list_lenses` read tool, and the `promote_collection_to_lens` /
    `demote_lens` / `set_document_default_lens` write tools (gated by
    `DOCRAY_MCP_ENABLE_WRITE`), with agent-facing docstrings explaining that
    promotion is a curation decision, not a mechanical one.
  - **Recorded decisions:** lenses ship flat (the `parent_lens_id` column exists
    but resolution is not implemented — D-1); the default lives on `documents`
    (D-2); `document_collections` remains shared, non-tenant navigation metadata,
    so a lens *name* is visible corpus-wide while a document's `default_lens_id`
    rides the existing `documents` RLS (D-3). The FR-2 preference for a composite
    FK was superseded by triggers: PostgreSQL 16 rejects `ON DELETE SET NULL` on a
    FK containing the generated companion column the composite form requires.

## [1.40.0]

### Fixed
- **Editing a document's authors failed every metadata job** (Claude Opus 4.8 via
  Claude Code). `document_metadata_history` records one row per changed field,
  gated by a CHECK listing the editable vocabulary. Migration 045 created it
  (title/description/tags) and 053 widened it for `lifecycle_state`; the
  authors-edit capability was later added to the writer
  (`persistence_doc_admin.update_document_metadata` calls `_record("authors", …)`)
  and to the GraphQL/MCP surfaces, but no migration widened the CHECK. So every
  `updateDocumentMetadata` that changed an author aborted its whole job with
  `CheckViolation: document_metadata_history_field_check` — 29 failed jobs in
  production before it was traced, and the two DB-backed tests that cover author
  edits had been red on `main`, which let a real defect hide among tolerated
  failures.

  Migration 118 admits `authors`. The deeper fix is the binding that was missing:
  the vocabulary now has one source of truth in code
  (`persistence_doc_admin.HISTORY_FIELDS`) and a drift guard
  (`tests/test_metadata_history_contract.py`) that reads the live constraint out
  of the catalog and fails the build if the two ever diverge again — including the
  reverse direction, an orphan value in the constraint that the code never writes.
  A parametrized test additionally proves each declared field is genuinely
  insertable, so the contract is asserted against real behavior, not just string
  equality.

- **Every backup failed on a table the backup role could not read** (Claude Opus
  4.8 via Claude Code). A backup COPYs every public base table elevated to the
  trusted writer `docray_batch`. Migration 106 granted that role access with a
  one-time `GRANT ALL ON ALL TABLES` and no `ALTER DEFAULT PRIVILEGES`, so
  `web_auth_sessions` (migration 112) — and any table added later — was invisible
  to it, and the whole backup aborted mid-COPY with `permission denied`. 24
  backups failed; the corpus had no recoverable snapshot. Migration 119 re-grants
  over all current tables/sequences and adds default privileges so every future
  table is auto-granted to `docray_batch`, closing the latent trap that re-armed
  on each `CREATE TABLE`. The backup path is also hardened
  (`application.backup.unreadable_tables` + a `create_backup` preflight): a
  missing grant now fails loudly, naming the offending tables, before any archive
  bytes are written — an incomplete backup is data loss on restore, never a silent
  skip. Regression: `tests/test_backup_grants.py`.

## [1.39.0]

### Added
- **MCP resources — `docray://` URIs and templates (Block 3b)** (Claude Opus 4.8
  (1M context) via Claude Code). The server previously declared only MCP's
  *tools* primitive; it now also declares **resources**, so a host can enumerate
  addressable handles with `resources/templates/list` and fetch one with
  `resources/read` instead of composing a tool call. Five surfaces
  (`entrypoints/mcp/resources.py`): `docray://doc/{document_id}`,
  `docray://doc/{document_id}/sentences/{start}-{end}`,
  `docray://doc/{document_id}/pages/{page}`, `docray://collection/{collection_id}`,
  and `docray://corpus/shards/{shard_count}`.

  This is **addressing, not capability**: every read goes through the same read
  tools, so the caller's bearer token and Postgres RLS scope the result
  identically, and a `resources/read` still lands its bytes in the caller's
  context — the resource layer improves addressing and host-side caching, not
  payload size. The whole-document URI therefore truncates **explicitly** at
  200 000 chars, naming the ranged template for the remainder, rather than
  silently. The ranged template is the one that matters at scale and is a thin
  wrapper over Block 3a's random access.

  Host-gated: declaring resources makes them available on the protocol, but a
  client must still surface `resources/*` before a `docray://` URI can be read —
  clients that expose only tools are unaffected. No schema change, no new
  dependency, no write surface (all reads, always registered).

- **Random-access sentence reads — ordinal range + page (Block 3a)** (Claude Opus
  4.8 (1M context) via Claude Code). `read_document_sentences` / `Document.sentences`
  were sequential-only (cursor walk): reaching a mid-document slice meant paging
  from ordinal 0. They now accept `fromOrdinal`/`toOrdinal` (inclusive ordinal
  range) and `page` (a source-page filter via `spans.page_number`, migration 049),
  so an agent can jump straight to sentences 4000–4200 or "page 50" in one call —
  the one genuinely-new capability behind the MCP-resource discussion (the
  resource/template layer is addressing ergonomics on top of this; a later block).
  The args are a `WHERE` extension on the existing `list_sentences` keyset query
  (the spans join and ordinal ordering already existed) — no schema-version change,
  no new tables. Citation handles (`ordinal`/`charStart`/`charEnd`) and verbatim
  text are unchanged; the sequential walk is still the default. A DB-backed test
  proves a range read returns exactly the same rows as slicing the full walk, and
  the page filter isolates one source page; `fromOrdinal > toOrdinal` is rejected
  (resolver + MCP tool). See `docs/mcp-service.md` §3 for the random-access example.

- **Durable NER corrections — Block A (P0)** (Claude Opus 4.8 (1M context) via
  Claude Code). The verification layer PURPOSE.md mandates ("AI proposes,
  verification decides") for named-entity recognition: a human (or a reviewing
  agent under write scope) records a correction — reclassify a type, reject a
  spurious mention, add a missed entity, or adjust a span boundary — and it
  *survives re-annotation*. spaCy `entity_mention` rows are ephemeral
  (`ON DELETE CASCADE` from `annotations`, rebuilt by every `reannotate`), so a
  fix written there would vanish; instead a correction is keyed by a stable span
  identity `(document_id, char_start, char_end)` in the new
  `entity_mention_correction` table (migration 115, RLS-scoped to the document's
  writable set), and the pure, deterministic `doc_ray.domain.ner_corrections.reconcile()`
  re-applies the correction log onto each fresh set of ML mentions. Corrections
  record the reviewer and the original ML value, so every fix is attributable
  and reversible. Design: `docs/proposals/entity-fabric-substrate-lens-projection.md`.

  The capability is now usable end to end. The annotate pipeline re-applies the
  log automatically at the tail of every run, so a fix made once holds across
  re-annotations. GraphQL exposes `correctEntityMention(documentId:, action:,
  charStart:, charEnd:, …)` (write) and `entityMentionCorrections(documentId:)`
  (the audit log); the MCP server exposes the matching
  `correct_entity_mention` (write-gated by `DOCRAY_MCP_ENABLE_WRITE`) and
  `entity_mention_corrections` (read) tools. Because `entity_mention` and `spans`
  are owned by the batch worker — `docray_api`/`docray_client` hold only `SELECT`
  on them — the API never writes mentions directly: it records the correction and
  enqueues a cheap `apply_corrections` job (new job kind; no spaCy, just the
  deterministic reconcile) that carries the fix through. Malformed corrections
  (unknown action, `reclassify` without a type) are rejected and audited before
  anything is recorded. Usage: README "Correcting the model's entities",
  `docs/mcp-service.md` §3.
- **A self-teaching MCP surface — server instructions, search coverage, steered
  errors (BL-145)** (Claude Opus 4.8 (1M context) via Claude Code). A user
  summarizing a full novel through the MCP server lost most of their effort to
  friction the server could have removed: high-frequency search terms flooded one
  region of the text and starved the rest, which *read as missing content* rather
  than as a bad query; the retrieval-not-reading model had to be re-derived
  mid-task; and a `search_sentences` cursor handed to `read_document_sentences`
  failed with an opaque rejection. The server now teaches all three. `FastMCP` is
  constructed with `SERVER_INSTRUCTIONS`, advertised at initialization, stating
  the retrieval model and the recommended path (`list_documents` → ranged
  `read_document_sentences` → rare-term `search_sentences`). Cursor errors now
  carry an actionable hint that cursors are per-tool and name the ordinal
  alternative. Five thin tool docstrings (`full_text_search`, `get_document`,
  `get_collection`, `entity_frequency`, `image_class_summary`) were expanded to
  teach their use, and `list_documents` now also returns `language` and
  `sentenceCount` so a document can be sized before it is read. No schema change.
- **Drift protection for new MCP surface** (Claude Opus 4.8 (1M context) via
  Claude Code). `src/doc_ray/tests/test_mcp_p0.py` holds every *future* tool to
  the same bar: one test fails the build if any registered MCP tool carries a
  description under 80 characters, another fails if a tool taking a `document_id`
  does not validate it locally (`_require_uuid`) before spending a network
  round-trip. Guidance that is only documented decays; these are enforced.
- **`read_document_outline` — the map that aims the ranged reader (BL-146)**
  (Claude Opus 4.8 (1M context) via Claude Code). Random access by ordinal has
  existed since Block 3a, but nothing told an agent *which* ordinals to ask for,
  so "read the ending" stayed a guess. The new tool (GraphQL: `documentOutline`)
  returns a short list of regions whose `ordinalStart`/`ordinalEnd` feed straight
  back into `read_document_sentences`, plus `pageStart` for the page reader.
  Structure comes from the ATX headings every extractor already normalizes into
  the stored text; the honest part is `source`, which distinguishes real
  `headings` from the `uniform` grid used where detection found none — measured
  across the corpus, PDF heading detection finds 723 in a technical book, 70 in
  *Princess of Dune*, and none at all in *Dune Messiah*, so an empty answer would
  fail the documents the feature exists for. A grid is labelled a grid and its
  titles are never presented as chapter names. No schema change: headings are
  parsed from `document_content`, and each offset is resolved onto its sentence
  by one indexed lookup (D-024-clean — no new fact-table aggregate).
- **`entity_profiles` — reconciled entities with the disagreement measured
  (BL-146)** (Claude Opus 4.8 (1M context) via Claude Code). `entity_frequency`
  returns raw NER, which splits one real-world entity across types and spellings
  and leaves the agent to merge it by hand. The new tool (GraphQL:
  `entityProfiles`) returns one row per entity — merged `surfaceForms`, the full
  `typeSpread`, the real `mentions` total — and reports `typeConsistency`, the
  dominant type's *measured* share of those mentions, never a model score.
  Against the live corpus `Arrakis` collapses from five rows into one at
  consistency 0.36 (LOC 514 / GPE 495 / PERSON 209 / NORP 179 / ORG 24), and 47
  of the top 200 entities score below 0.9. `resolved` states whether the canonical
  registry backs the row at all. Only normalization-equivalent spellings fold
  together (the same `form_norm` key `form_index` uses): `Duncan` and `Duncan
  Idaho` stay separate, because relating them is coreference and guessing it
  would be fabrication.
- **More drift protection, and the drift it already caught** (Claude Opus 4.8
  (1M context) via Claude Code). `test_mcp_p1.py` adds three guards: every tool
  that embeds a GraphQL document must be exercised by the schema-contract test
  (which validates it against the live Strawberry schema), every read tool must
  appear in the `docs/mcp-service.md` inventory, and any tool offering
  `response_format` must validate it. Turning them on immediately failed:
  `document_images`, `image_class_summary`, `visual_captions`, and
  `cross_modal_image_search` were in neither the schema-contract list nor the
  inventory — four shipped tools whose GraphQL nobody was checking. Both gaps are
  closed. `persistence_read_structure.py` was also added to the D-024 scan set.

### Fixed
- **`entity_profiles` was dead for every real caller — `form_index` never granted
  to `docray_client` (BL-147)** (Claude Opus 4.8 (1M context) via Claude Code).
  BL-146's `entityProfiles` joins `form_index` → `canonical` to report whether an
  entity is registry-backed. The API and webapp run as `docray_api`, which
  inherits `docray_client` (migration 030); migration 097 added `canonical` to
  that role's SELECT allowlist when the entity overlay needed it, but
  `form_index` — its sibling from the same layer — was never added. Every
  `entityProfiles` / `entity_profiles` call therefore raised
  `InsufficientPrivilege: permission denied for table form_index` in production
  while the entire suite stayed green. Migration `117` grants it (grant-only, no
  schema-version change, mirroring 097's reasoning). The durable fix is the
  accompanying invariant test: it asserts `docray_client` can SELECT **every**
  table the structure read path touches, so it fails on the grant itself rather
  than depending on fixture data. Root cause is the recurring one here — the read
  path was verified as a privileged role (`docray_batch`, which holds
  `BYPASSRLS`), and the behavioural tests could not reach the join because
  `entity_profiles` returns early when there are no profiles to enrich.

### Changed
- **`response_format: "concise" | "detailed"` on the bulk readers (BL-146)**
  (Claude Opus 4.8 (1M context) via Claude Code). `read_document_sentences` and
  `search_sentences` accept a verbosity argument. `detailed` is the default and
  is byte-for-byte the historical shape, so **no existing caller changes**.
  `concise` drops the row id and the per-row cursor (the last row keeps one — the
  only cursor paging uses) and, for a document-scoped search, the redundant title
  and `documentId`. Measured on a realistic 200-sentence page: 47,314 → 29,876
  characters, a **37% reduction** (our number on our payloads; the ~⅔ figure
  Anthropic report for their own tooling does not transfer). The saving is taken
  on the wire too — the tool requests a smaller GraphQL selection set rather than
  fetching and discarding. `ordinal`, `charStart`, and `charEnd` survive in every
  mode: a cheaper answer that cannot be cited is not a cheaper answer.
- **BREAKING (MCP tool contract): `search_sentences` returns an object, not a
  list** (Claude Opus 4.8 (1M context) via Claude Code). The tool previously
  returned a bare list of KWIC hits, which made a truncated result
  indistinguishable from a genuinely sparse one — the exact failure that cost the
  novel-summarization run its coverage. It now returns:

  ```jsonc
  {
    "hits": [ /* unchanged KWIC rows: id, documentId, ordinal, pre, hit, post */ ],
    "coverage": {"hit_count": 200, "ordinal_min": 5, "ordinal_max": 1904,
                 "limit": 200, "limit_reached": true},
    "note": "Hit the limit — a common term can flood one region of the text …"
  }
  ```

  **Migration.** Callers that iterated the result directly must index `["hits"]`:
  `for s in await search_sentences(...)` → `for s in (await search_sentences(...))["hits"]`.
  The hit rows themselves are byte-for-byte unchanged, so nothing downstream of the
  loop needs touching. In-tree callers (`briefing_pack`, `entity_dossier`,
  `document_evidence` in `entrypoints/mcp/compose.py`) are already updated.
  `coverage.limit_reached` is the signal to act on: when true, the result is
  capped, not complete — narrow the term or read the range with
  `read_document_sentences(from_ordinal=…, to_ordinal=…)`. The GraphQL
  `searchSentences` field is **unchanged**; this affects the MCP tool only.

### Fixed
- **MCP resources were invisible to clients — `resources/list` returned `[]`**
  (Claude Opus 4.8 (1M context) via Claude Code). The resource layer shipped with
  five surfaces, *all* of them parameterized URI templates. The SDK keeps concrete
  and templated resources in **disjoint registries**: `resources/list` returns only
  the concrete ones, templates come back solely from `resources/templates/list`.
  So a client enumerating `resources/list` — the usual way a UI populates its
  resource picker — got an empty array and rendered "no resources", even though
  the capability was advertised and all five templates were live.

  Root cause was twofold: no parameterless entry point existed, **and the tests
  asserted `list_resource_templates` twice while never calling `list_resources`**,
  so a client-invisible surface passed the whole suite. Added three concrete,
  listable entry points — `docray://corpus/documents` (the document index),
  `docray://corpus/collections`, and `docray://corpus/manifest` (shard map at the
  default width of 8) — with the templates retained as drill-down targets. The
  regression tests now assert `resources/list` is non-empty, pin the concrete URI
  set, and enforce the structural invariant that every parameterless spec lands in
  the concrete registry and every braced one in the template registry (the two
  never overlap). Note the remaining limit is client-side: declaring resources
  puts them on the protocol, but a host must still surface `resources/*`.

- **MCP tests were order-dependent and red after the webapp suite (BL-144)**
  (Claude Opus 4.8 (1M context) via Claude Code). `test_mcp_corpus.py` passed
  30/30 alone and failed en masse in the full run — originally misfiled as a
  pinned-SDK breakage. The real cause: the MCP tool tests monkeypatch
  `httpx.AsyncClient` to a stub callable, and the SDK's `mcp.client.streamable_http`
  module evaluates `httpx.AsyncClient | None` in its body at import time. Any
  *lazy* import of that transport landing inside a patched test therefore
  evaluated `<lambda> | None` and raised `TypeError: unsupported operand type(s)`.
  Fixed by importing the transport eagerly in `src/doc_ray/tests/conftest.py`,
  before any test can patch `httpx` — the suite is now order-independent.

## [1.38.0]

### Added
- **Corpus sharding — `corpusManifest` + `corpusShard` for map-reduce reads**
  (Claude Opus 4.8 (1M context) via Claude Code). New GraphQL queries and MCP
  read tools that partition the visible corpus into `shardCount` deterministic,
  disjoint parts via `hashtext(id) mod shardCount`, so an orchestrator can fan
  out one worker per shard over the whole corpus. `corpusManifest(shardCount)`
  returns per-shard document counts that sum to `totalDocuments` (a verifiable
  coverage check); `corpusShard(shardIndex, shardCount, limit, after)` returns
  one shard's document summaries, keyset-paged, newest first. Both are
  RLS-scoped — a shard is the intersection of the hash partition and what the
  token may read. The shard predicate is added to the existing `list_documents`
  keyset query; the manifest is a bounded `GROUP BY` over `documents` (a
  dimension table, not a D-024-flagged fact table). Determinism, completeness,
  and disjointness are proven by DB-backed tests (each seeded document lands in
  exactly the shard its hash predicts; the shards re-union to the manifest
  total). Composes with `read_document_sentences`: a shard yields ids, each
  document is then read in full. See `docs/mcp-service.md` §3 for the map-reduce
  example. No schema-version change (additive queries + existing tables).

- **`read_document_sentences` — exhaustive sequential text read over MCP** (Claude
  Opus 4.8 (1M context) via Claude Code). `search_sentences` is term-filtered, so
  an agent could never walk a document end-to-end — the concrete cause of
  coverage gaps in whole-document summarization. The new always-on MCP read tool
  wraps the existing `Document.sentences(limit, after)` field (no schema change):
  it returns every sentence in `ordinal` order, keyset-paged, so walking `after`
  cursors to exhaustion covers 100% of the text with no gaps and no keyword
  flooding. Text is returned **verbatim** (not clipped like KWIC windows —
  exhaustive delivery is the contract; payload is bounded by `limit`), and each
  sentence carries `ordinal` + absolute `charStart`/`charEnd` offsets so any
  excerpt stays citable across slices. A completeness test drives the tool with
  successive cursors and asserts the walk reconstructs the full ordinal sequence.
  See `docs/mcp-service.md` §3 for the walk example. (A resource-template form —
  `docray://document/{id}/sentences/{start}-{end}` — is a future block; this tool
  closes the gap now and also scales to a multi-document corpus.)

- **Corpus view modes — table, compact, gallery, grouped** (Claude Opus 4.8
  (1M context) via Claude Code) — BL-143. The corpus page shipped as a single
  dense table: one ~156px row per document (cover thumbnail + wrapped title +
  a four-line, near-identical analyzer string), so only ~5 documents fit a
  screen and a growing library stopped being browsable — a gap found by
  inspecting the live `/corpus` with Playwright. The page now offers four
  layouts over the same rows and the same bulk-selection model, driven by URL
  query params so a view is shareable and bookmarkable: `view`
  (`table`/`compact`/`gallery`/`grouped`), `sort`
  (`size`/`recent`/`title`/`sentences`/`pages`), `density`
  (`comfortable`/`compact`), and `group_by` (`type`/`language`/`status`).
  `compact` is one line per document (~3–4× the density); `gallery` is a
  cover-forward grid that reuses the already-rendered first-page covers;
  `grouped` sections the list with per-group counts and sentence subtotals.
  Every param is whitelisted — an unknown value degrades to its default and the
  `sort` value never reaches SQL — so a hand-edited URL is always safe. These
  are webapp presentation over the read model; the GraphQL API and MCP already
  return every field the modes use (title, type, language, char/page counts,
  sentence count, status, cover), so any client can build equivalent views.
- **Editable document authors, and collection rename/delete** (Claude Opus 4.8
  (1M context) via Claude Code). Two gaps surfaced by connector use:
  authors were write-once at ingest (no edit path anywhere), and a collection,
  once created, had an immutable name and could never be deleted — so a
  mis-typed name (e.g. an HTML-escaped `"AI &amp; Business"`) was permanent.
  - **Authors** are now editable through the existing metadata path:
    `updateDocumentMetadata` gains an `authors: [String!]` argument (ordered
    replace-set — author order is meaningful; `[]` clears), applied by the
    worker + `persistence_doc_admin.update_document_metadata` (upserts the
    `authors` dimension, rewrites the `document_authors` junction, records a
    `documentMetadataHistory` row). The MCP `update_document_metadata` tool
    gains the same `authors` param. No migration — it uses the existing 4NF
    author tables.
  - **Collections** gain `updateCollection(collectionId, name?, description?)`
    and `deleteCollection(collectionId): Boolean` GraphQL mutations, plus the
    matching MCP `update_collection` / `delete_collection` write tools. Rename
    to an already-used name is a clean error (names are globally unique).
    `deleteCollection` removes **only the container** — member documents are
    never touched (`document_collection_members` is `ON DELETE CASCADE`, so
    only membership rows cascade), covered by an explicit regression test.
  Both follow D-030: reads unaffected; writes need `write` scope (+ per-document
  grant for author edits) and are billing-classified. See `docs/mcp-service.md`
  §3 and the MCP write examples.

- **MCP exposes the collection tools** (Claude Opus 4.8 (1M context) via Claude
  Code) — D-030. doc-ray has a native `DocumentCollection` container, but MCP
  surfaced none of it, so connectors fell back to ad-hoc tags. MCP now has two
  always-on read tools — `list_collections`, `get_collection` (with member
  documents) — and, behind `DOCRAY_MCP_ENABLE_WRITE`, three write tools:
  `create_collection`, `add_document_to_collection`,
  `remove_document_from_collection`. They forward the existing GraphQL
  collection fields, so the same guards apply: reads are RLS-scoped;
  `create_collection` needs `write` scope; add/remove additionally need a write
  grant on the document and a `manual` (not smart-rule) collection. A
  schema-contract test validates every collection document against the live
  schema. Agents can now build real named containers instead of tag pseudo-
  collections (see `docs/mcp-service.md` §3 for signatures and an end-to-end
  example). Sharing mutations and destructive/admin verbs remain unexposed.

- **OAuth connectors can obtain `write`, bounded by the user's RBAC** (Claude Opus
  4.8 (1M context) via Claude Code) — D-032. The MCP write tools (D-030) were
  inert for OAuth-connected agents: the Authorization Server only ever minted
  `read` (`SUPPORTED_SCOPES = ("read",)`), so every mutation 401'd on the scope
  check before ownership was even consulted — a Claude Desktop connector couldn't
  rename a title even for its own documents. `SUPPORTED_SCOPES` is now
  `("read", "write")`, and on the interactive `authorization_code` grant the
  issued scope is **not** what the client asked for but the *consenting user's*
  own RBAC capability: `OAuthServerService.issue_code(..., principal_scopes=…)`
  binds it via `_bound_scope`, `webapp/oauth_web.py` derives it with the new
  `LocalAuthService.scopes_for_principal` (the same `scopes_for_actions`
  derivation `login` uses, D-027), and the consent screen shows what will be
  granted. A `member`/admin connector gets `write`; a `reader` stays read-only
  however the request is framed; an unresolvable principal degrades to read-only
  (never fail-open). `write` is still only the coarse "may mutate anything" gate —
  the row-level `document_acl` decides which documents. To rename via a connector
  end-to-end: reconnect doc-ray so the user re-consents (the token now carries
  `write`), and ensure `DOCRAY_MCP_ENABLE_WRITE=true`.

- **Organization corpus — opt-in tenant-wide document visibility** (Claude Opus
  4.8 via Claude Code) — BL-139. Per-owner isolation (1.34.0) and sharing
  (1.37.0) covered private documents and person-to-person grants; this adds the
  third scope. `documents.visibility` (migration 110) is `private` by default;
  an owner (or tenant admin) publishes a document to their whole organization
  with the new `setDocumentVisibility(documentId, visibility)` mutation and the
  Share page's "Publish to my organization" control. Every principal in the
  tenant can then read it — and nobody outside it, because the org-read branch
  in `docray_readable_documents()` is scoped to `tenant_id`.

  This is the **safe inverse** of the migration-076 leak that started this whole
  line of work: 076 defaulted every document to visible and subtracted
  exceptions (so one tenant with no exceptions saw everything); here documents
  default to private and only a deliberate, owner-authorized flip to `org`
  widens them. The org-read branch is gated on `visibility = 'org'`, not on
  merely holding the read permission, so it cannot regress into 076 — the
  anti-regression guard in `test_reader_visibility.py` still passes.

  The visibility write goes through `docray_set_document_visibility`, a
  SECURITY DEFINER function that re-checks owner-or-admin authority
  (`docray_can_share_document`) in the database and is the column's only write
  path — so `docray_client` needs no UPDATE grant on `documents` and an
  annotate-grantee cannot publish a document lent to them. `visibility` is
  exposed on the GraphQL `Document` type; every change is audited (D-030).

### Fixed
- **Webapp test suite green again — stale test stubs and copy re-synced to the
  app** (Claude Opus 4.8 (1M context) via Claude Code) — BL-140. `tests/test_webapp.py`
  had 16 tests red on main from drift between the tests and the code they cover
  (no production behavior was wrong — the tests were stale). Two families: SQL
  stub drift, where `fake_q`/`fake_q1` still matched pre-rollup queries (live
  `count(*)` / `GROUP BY`) so the lexicon and reader pages rendered empty and
  content assertions failed — re-derived from the current SQL (the `lexeme_stats`
  and `document_stats` rollups of migration 098, and spans-first `JOIN` reads);
  and copy drift, where assertions checked prose the templates had since
  reworded — repointed at the live text. Added a regression guard
  (`test_reader_and_lexicon_stubs_cover_the_apps_data_queries`) that records any
  query no stub handler recognises and fails loudly when a live data query falls
  through to the empty default, so this silent-drift class can't recur. No
  production code, API, schema, or version change.
- **Member corpus reads no longer 500 — SECURITY DEFINER RLS helpers run with
  BYPASSRLS** (Claude Opus 4.8 (1M context) via Claude Code) — BL-142. A
  non-admin member browsing `/corpus` hit a `stack depth limit exceeded` crash:
  `docray_readable_documents()` and its siblings are SECURITY DEFINER functions
  that read `FROM documents`, and under FORCE RLS (1.36.0) their body re-enters
  the documents policy — which calls them again — recursing without bound. It
  only bites a non-admin caller (admins short-circuit on `docray_is_admin()`) and
  only when the function owner is a non-superuser, which is exactly why the
  superuser-owned CI database never reproduced it — the same managed-vs-superuser
  gap as migrations 032/106. Migration 114 reassigns every SECURITY DEFINER
  helper that reads a forced-RLS table to the BYPASSRLS batch writer
  `docray_batch` (they compute access decisions / corpus-wide stats, so they are
  meant to see all rows), with a dynamic sweep and a RAISE guard that makes the
  invariant enforceable. A separate 500 in `documents.html` — `"{:,}".format`
  on a `None` `n_sentences` — is fixed with the same `or 0` guard the row above
  it already used. Both land regression tests (`test_forced_rls.py` reproduces
  the recursion self-contained and asserts the schema invariant; `test_webapp.py`
  covers the None render).
- **Member uploads no longer rejected by row-level security — the jobs queue is
  trusted infrastructure** (Claude Opus 4.8 (1M context) via Claude Code) —
  BL-141. Every non-admin `member` upload failed with `new row violates
  row-level security policy for table "jobs"`. The `jobs` INSERT policy (043)
  gated a document-less job behind `docray_is_admin()`, on the assumption that
  "no document_id" meant "corpus-wide backup/restore" — but an *upload's* ingest
  job is also document-less at enqueue time (the document does not exist until
  the worker creates it), and under the ownership model (1.34.0) it is enqueued
  by a non-admin member. No `document_id`-keyed policy could ever admit it (a
  brand-new row's id is never in the writable set — the wall migration 106
  documents). The fix adopts how mature systems treat an async queue:
  authorization happens once, at the API boundary (`mutation_guards`), and the
  enqueue runs as the trusted writer `docray_batch` (`enqueue_async` elevates
  with `SET LOCAL ROLE`). The queue table is not a user-facing resource, so
  migration 113 drops the user-facing INSERT policy entirely — with FORCE RLS
  that leaves the trusted writer as the only enqueuer — and RLS on `jobs` now
  governs **read visibility only**: a new `jobs.enqueued_by` records the acting
  principal so a member sees the jobs they enqueued (including their document-
  less ingest) without seeing anyone else's. Regression coverage lands the
  production error as a failing test in `test_jobs_trusted_writer.py`.
- **Webapp logins no longer wiped on restart — durable Postgres session store**
  (Claude Opus 4.8 (1M context) via Claude Code). The webapp's per-user login
  sessions lived in a process-local dict (`InMemoryAuthSessionStore`): the Flask
  cookie carries only an opaque session id, and the matching server-side record
  was in memory, so every webapp container restart (and any second replica)
  logged all users out. `webapp/auth_session.py` is now a swappable-backend
  proxy; production persists sessions in a new `web_auth_sessions` table
  (migration 112) via `PostgresAuthSessionStore`, selected by
  `DOCRAY_WEBAPP_SESSION_STORE` (compose defaults to `postgres`; `memory` keeps
  the in-process fallback for tests/local single-instance use). Same server-side
  semantics — logout and TTL expiry still revoke immediately — now durable and
  replica-safe. **Operator action:** apply migration 112 and set (or accept the
  compose default of) `DOCRAY_WEBAPP_SESSION_STORE=postgres`.

## [1.37.0]

### Added
- **MCP write tools — reprocessing + curation mutations, opt-in and off by
  default** (Claude Opus 4.8 (1M context) via Claude Code) — D-030. The MCP
  server can now expose 12 write tools (`reannotate`, `reextract_document`,
  `annotate_document`, `semantic_enrich_document`, `canonicalize_document`,
  `suggest_seeds`, `suggest_document_metadata`, `update_document_metadata`,
  `bulk_tag_documents`, `decide_seeds`, `decide_document_metadata_suggestions`,
  `seed_approved`) plus a `get_job` read tool to poll enqueued jobs. They are
  registered **only** when `DOCRAY_MCP_ENABLE_WRITE=true`; with the flag off the
  MCP surface is provably read-only — the tools do not even appear in
  `tools/list`. The new `entrypoints/mcp/mutations.py` forwards each caller's
  bearer token into the existing GraphQL mutation and does **not** re-implement
  authorization: the API's `_require_write_audited` (authenticated principal +
  `write` scope + billing entitlement, audited) and `_require_document_write`
  (per-document ACL) guards remain authoritative, and a caller lacking scope or
  grant receives a typed `permission_denied`. Only reversible verbs are exposed —
  destructive/admin mutations (delete, archive, backup/restore, cancel_job) and
  `ingestDocument` are deliberately not registered. A schema-contract test
  validates every hand-written MCP GraphQL document against the live API schema
  so operation strings cannot silently drift from the resolvers. Enable it by
  setting `DOCRAY_MCP_ENABLE_WRITE=true` on the `mcp` service; see
  `docs/mcp-service.md` §3 for tool signatures and end-to-end examples.

- **Owner-driven document sharing** (Claude Opus 4.8 via Claude Code) — Phase 3.
  Per-owner isolation (1.34.0) gave each user a private corpus, but only the
  admin CLI could write `document_acl` rows, so a user had no way to hand a
  document to a colleague. New `shareDocument(documentId, email, permission)` and
  `revokeDocumentShare(documentId, email, permission?)` GraphQL mutations, a
  `documentShares(id)` query, and a webapp Share page (`/doc/<id>/sharing`, a new
  self-contained `sharing_web` module) let an owner grant `read` or `annotate` to
  a colleague by login email and revoke it later.

  Authorization is enforced at the database, per Phase 2's rule: `document_acl`
  becomes `FORCE ROW LEVEL SECURITY` (migration 108) with policies scoping every
  grant/revoke to documents the caller **owns** (or, for an admin, any in the
  tenant) — so granting `docray_client` the INSERT it now needs cannot become a
  way to forge a grant on someone else's document. The mutation guard
  (`_require_document_share`) is the clean-error layer on top; the RLS policy is
  the backstop. `admin` and `redact` are never grantable this way, and `delete`
  is excluded — sharing lends a document, it does not transfer control.

  Sharing is by login email, resolved through tenant-scoped SECURITY DEFINER
  helpers (`docray_principal_by_email`, `docray_email_for_principal`) rather than
  a table grant, so the API role never gains blanket read on `principal_credentials`
  (which also holds password hashes) and email lookups cannot cross tenants. Every
  grant and revoke is written to the append-only audit log. The admin CLI's
  `grant_document` is unaffected: `auth_admin` now assumes the `docray_batch`
  role (BYPASSRLS), the same trusted-writer identity the worker uses (D-029).

## [1.36.0]

### Security
- **RLS is now enforced by the database, not by the application tier** (Claude
  Opus 4.8 via Claude Code) — BL-138. Every corpus table had `ENABLE ROW LEVEL
  SECURITY`; none had `FORCE`. Both the API and the webapp connect as `docray`,
  which *owns* those tables, and Postgres exempts a table owner from its own
  policies unless the table is forced — so any code path that failed to
  `SET ROLE` read the corpus with no policy applied at all, not merely as an
  anonymous caller. Ownership (1.34.0) fixed the policy that leaked; it did not
  change the fact that a missing `SET ROLE` bypassed *every* policy.

  Migration 106 forces RLS on all 55 RLS-enabled tables and gives the trusted
  batch writer (worker, CLIs — which annotate across every tenant with no
  request principal) an *explicit* exemption: a named `docray_batch` role
  carrying `BYPASSRLS`, which `adapters/persistence.connect()` assumes. The
  exemption used to be incidental — whoever happened to own the table — and is
  now a role attribute an audit can see.

  A batch-writer *policy branch* was built and rejected on evidence: the write
  predicates are `id IN (SELECT … FROM documents)`, which a brand-new row can
  never satisfy (INSERT); `INSERT … RETURNING` re-applies the SELECT policy to a
  row not yet in the policy's snapshot; and the permissive SELECT policy needed
  to fix that OR-combines into the read path and defeats the semi-join pullup
  migration 018 documents as load-bearing on the ~29M-row annotation tables. A
  role attribute expresses "trusted writer" exactly, with no read-path cost.

  **Operator action required on the managed cluster:** `BYPASSRLS` can only be
  granted by a superuser, and the migration runner (`docray`) is not one. The
  migration deliberately RAISES rather than forcing RLS without a working batch
  writer (a half-applied state stops every ingest job). If it raises, run once
  as the cluster superuser and re-run migrations: `ALTER ROLE docray_batch
  BYPASSRLS;` (see `db/migrations/106_force_rls.sql` and production-setup.md).

### Fixed
- **MCP tools no longer forward a stale bearer token after a mid-session refresh**
  (Claude Opus 4.8 (1M context) via Claude Code). On stateful Streamable-HTTP,
  a tool coroutine runs in the session task created at `initialize`, not the POST
  task the auth middleware ran in — so the `caller_token` contextvar the
  middleware set never reached the tool, and whichever token opened the session
  was frozen for the tool's lifetime. Because OAuth access tokens live 15 min
  (`DOCRAY_API_JWT_TTL_SECONDS=900`) while the MCP session outlives them, every
  tool call after the client's first silent refresh forwarded the expired token:
  the MCP hop stayed `200` while the MCP→GraphQL hop `401`'d, so agents saw the
  corpus "expire the session" ~15 minutes in. Token resolution moved to a new
  `entrypoints/mcp/tokens.py` that reads the credential from the request the SDK
  is currently dispatching (`request_ctx`), falling back to the contextvar only
  for transports with no per-call HTTP request (stdio). The middleware still
  authenticates every POST before dispatch, so validation is unchanged.

## [1.35.0]

### Added
- **The `member` role — a registered user who can own a corpus** (Claude Opus
  4.8 via Claude Code). Per-owner isolation (1.34.0) exposed the other half of
  the problem: the default signup role `reader` carries only the `read`
  permission, and every local login minted `scopes={"read"}` while
  `_require_write_audited` refuses any mutation without `write`. A
  self-registered account could therefore never ingest anything, so isolation
  handed new users a permanently *empty* corpus rather than a clean one.

  Migration 104 adds `member` (read + annotate + delete), makes it the
  registration default, and promotes the humans who already self-registered
  under the old default — scoped to principals holding a local credential, so
  SCIM/OIDC-provisioned readers and service principals keep the read-only role
  they were deliberately given. `reader` stays genuinely read-only. Token
  scopes are now *derived* from the principal's own RBAC permissions
  (`scopes_for_actions`) instead of hard-coded, so re-granting a role changes
  what its members may do with no code change, and admins get `write` for free.

  This is not a privilege escalation: `annotate`/`delete` are consulted by no
  read or write policy — only `docray_is_admin()` reads the permission tables —
  so a member's reach stays exactly the documents they own or were granted,
  enforced per document by `docray_writable_documents()`.

### Fixed
- **Email verification no longer downgrades a new member to read-only** (Claude
  Opus 4.8 via Claude Code). `verify_local_email` hard-coded `frozenset({"read"})`
  when minting the post-verification auto-login token, so a member's very first
  session could not upload — the one thing a new account exists to do. The
  verification result now carries the same derived scopes `login` produces.
- **Ingest ownership wiring is now covered by tests** (Claude Opus 4.8 via
  Claude Code). 1.34.0 threaded the uploader from `ingestDocument` through the
  job payload to `insert_document` without a test on that path. Added: the
  mutation stamps the request principal into the payload; the handler threads it
  to the pipeline and coerces an absent owner to `None` rather than the string
  `"None"`; and `ingest_file` stamps ownership on both the success path and
  `record_failure`, so a failed extraction is not stranded where its uploader
  cannot see it.

## [1.34.0]

### Security
- **Every registered user could read the entire corpus** (Claude Opus 4.8 via
  Claude Code). doc-ray runs open registration, but migration 076's
  role-permission branch granted any principal holding the `read` action —
  which is every `reader`, the default signup role — visibility of the whole
  tenant corpus minus documents tagged `sensitivity=sensitive`. With one
  tenant, zero `document_acl` rows and zero sensitivity tags, that predicate
  narrowed nothing: three self-registered accounts could read all 192
  operator-ingested documents. 076 described itself as a stopgap for a granting
  flow that was never built.

  Migration 102 replaces the blanket branch with ownership.
  `documents.owner_principal_id` is now the read boundary — a document is
  readable by its owner, an explicit ACL grantee, or a tenant admin — and the
  same owner branch joins `docray_writable_documents()` so owning implies
  curating. `NULL` ownership means *unowned*: admin-only, and deliberately
  reserved as the seam for the future org corpus, so `tenant_id` stays free to
  mean "organization" and introducing orgs re-parents nothing. Uploads carry
  the uploader from `ingestDocument` through the job payload to
  `insert_document`, because the worker has no request principal to consult by
  the time it runs; operator CLI ingest leaves documents unowned on purpose.
  `scripts/assign_document_owner.py` moves existing documents into a user's
  corpus by login email (dry-run by default), keeping deployment-specific data
  out of a versioned migration.
- **The webapp no longer runs principal-less requests with owner privileges**
  (Claude Opus 4.8 via Claude Code). `_apply_principal` returned early when no
  principal resolved, leaving the connection on `DATABASE_URL`'s role — which
  owns the tables. No table in `db/migrations/` is `FORCE ROW LEVEL SECURITY`,
  and Postgres exempts the owner from RLS without it, so that path read the
  corpus with no policy applied at all. The role is now dropped unconditionally
  and the principal set to empty, so such a request fails closed. Reachability
  was limited to auth-exempt endpoints (`DOCRAY_WEBAPP_REQUIRE_AUTH=true`
  blocks anonymous corpus reads), which is why this is a hardening fix rather
  than a second incident; the missing `FORCE ROW LEVEL SECURITY` itself is
  tracked as BL-138.

### Fixed
- **`crossModalImageSearch` no longer leaks `No module named 'open_clip'`**
  (Claude Fable 5 via Claude Code). The GraphQL read path lazily imports the
  Tier-2 vision encoder at query time, but `open_clip`/`torch` are deliberately
  absent from the API image (they belong to the GPU worker), so MCP callers of
  `cross_modal_image_search` got a raw `ModuleNotFoundError` straight through
  GraphQL. The encoder factory now probes the vision stack with `find_spec`
  before any import and raises a typed `VisionStackUnavailableError` naming
  what is missing and how to deploy it; the MCP layer classifies both the typed
  message and the legacy raw leak onto a new non-retriable `unsupported` error
  kind with a hint steering agents to the text-side tools instead of retrying.
  Regression tests cover the encoder factory, the GraphQL wire contract, and
  the MCP classification of both message shapes.
- **Database ERD & data dictionary caught up with the live schema** (Claude
  Fable 5 via Claude Code). `docs/DATABASE.md` had drifted to 1.22.0/73
  migrations because the generator's own curated inputs were stale: 16 live
  tables (jobs, collections, canonicalization, layout runs, local-auth,
  SCIM/SAML) had never been added to its groups, the migration list stopped
  at 071, and the RLS table count was hard-coded. The generator now documents
  all 69 tables in five new/extended §5 groups, carries three new §3 Mermaid
  ERDs (canonicalization & curation, jobs & operations, authentication &
  identity), computes the RLS count from the catalog, and lists migrations
  through 077; `docs/doc-ray-database-erd.svg` was rebuilt to the full
  1.24.0 scope. Curated data moved to `scripts/_data_dictionary_content.py`
  to stay within the D-015 cap.

- **Backup jobs no longer starve their own heartbeat** (Claude Fable 5 via
  Claude Code) — BL-135. Progress reports wrote through the worker's main
  connection, so inside the backup handler's minutes-long REPEATABLE READ
  snapshot transaction each per-table report joined that transaction and held
  the jobs-row lock until commit — blocking the heartbeat on the same row
  until the reaper stole the job from a *living* worker ("could not complete;
  claim may have been lost", three attempts exhausted per backup). Progress
  now flows through the heartbeat (side) connection under a shared mutex,
  committing immediately and never joining handler transactions. The
  regression test drives a real snapshot-isolation handler and proves the
  jobs row stays unlocked (FOR UPDATE NOWAIT) with progress visible
  mid-transaction.
- **`createCollection` no longer 500s on a missing RETURNING row or scalar
  smart-rule tags** (Claude Fable 5 via Claude Code) — the mutation guards
  the INSERT's RETURNING row and normalizes non-list smart-rule tag values.
- **Package version reconciled with the schema** (Claude Fable 5 via Claude
  Code) — `doc_ray.__version__` had been left at 1.22.0 while migrations
  075/077 moved the schema to 1.24.0, leaving
  `scripts/check_version_consistency.py` red on main; the 1.23.0/1.24.0
  release sections below were reconstructed from the git history at the same
  time.

### Changed
- **Modernist design-system refresh across all webapp templates**
  (Claude Fable 5 via Claude Code) — typography, layout, and navigation pass
  over the browser UI; no route or data-contract changes.

## [1.33.0] - 2026-07-16

### Added
- **D-024 read-path aggregate gate + read-model layer generalized across the
  GraphQL/MCP surfaces** (Claude Opus 4.8 (1M context) via Claude Code). The same
  live-aggregation-over-fact-tables defect fixed for the webapp in 1.31.0/1.32.0
  was found by an audit across every read surface. This release turns the fix
  into an enforced architecture (ADR **D-025**): `scripts/check_read_path_aggregates.py`
  fails the build if any read module (`webapp/`, `entrypoints/api/`,
  `entrypoints/mcp/`, the read adapters) aggregates a large fact table
  (`lexeme_occurrences`/`spans`/`tokens`/`entity_mention`/`chunk`/`embedding`/`sentences`)
  live, with a small reasoned allowlist for single-key-bounded queries. Two more
  rollups back it: `document_stats.n_tokens` and per-tenant `entity_frequency_stats`
  (migration 100). Resolvers/loaders now read the rollups: GraphQL `list_lexemes`
  → `lexeme_stats`; `list_documents`/`sentenceCount`/`tokenCount` and the collection
  document lists → `document_stats`; `entityFrequency` (corpus-wide) →
  `entity_frequency_stats`; the webapp corpus/collections lists → `document_stats`;
  and the `/collections` landing counts smart collections without hydrating rows.
  Tracked separately (different layers): the `Lexeme.occurrences` N+1 (needs
  request batching) and `searchSentences`' leading-wildcard `ILIKE` (needs a
  trigram/FTS index).

## [1.32.0] - 2026-07-16

### Fixed
- **Document reader + Lexicon no longer aggregate `lexeme_occurrences` on every
  read** (Claude Opus 4.8 (1M context) via Claude Code). Same disease as the
  corpus band (1.31.0): live aggregation over the 17.5M-row table on read paths.
  Confirmed live via `EXPLAIN ANALYZE`/timings as `docray_api` + admin:
  - The Lexicon (`/lexemes`) joined `lexemes` → `lexeme_occurrences`, `GROUP BY`
    lexeme, `ORDER BY uses DESC` on every load — **~37 s**.
  - The reader sidebar recomputed a document's POS distribution (~740 ms) and top
    verbs (~464 ms) on every page navigation of a 1,250-page document.
  - The original-page overlay (`?view=original`) materialised **all 448k**
    occurrences of the document before filtering to one page (~500 ms grammar,
    and `attach_page_blocks` ~1.7 s), and ran even in the image-overlay view where
    sentence blocks are never rendered.
  Migration 098 adds `lexeme_stats(tenant_id, lexeme_uid, uses)` (per-tenant,
  ranked index) and `document_stats.pos_counts`/`top_verbs` (JSONB, document-level,
  RLS-scoped). Reads drop to ~1.5 ms (Lexicon) / ~1.6 ms (sidebar). The overlay
  and block queries now drive from `spans(document_id, page_number)` (indexed) —
  ~12 ms — and `attach_page_blocks` is skipped entirely in the original view.
  Rollups refresh in the trusted batch writer, never on the read path.
- **`/doc/<id>?view=original&overlay=entities` no longer 500s** (Claude Opus 4.8
  (1M context) via Claude Code) — `permission denied for table canonical`. The
  entity-overlay query joins the shared `canonical` registry to resolve a linked
  entity's preferred name, but `docray_client`'s SELECT allowlist (018_rbac.sql)
  predated the `canonical` table (021) and never included it. Migration 097 grants
  it, consistent with the `lexemes`/`authors`/`tags` registries; confidentiality
  is unchanged (the query only reaches canonical rows via RLS-scoped entity links).

## [1.31.0] - 2026-07-16

### Fixed
- **"The Corpus" landing page no longer scans 17.5M rows per request**
  (Claude Opus 4.8 (1M context) via Claude Code). `GET /corpus` recomputed four
  corpus-wide aggregates on every load; two of them (`count(DISTINCT lexeme_uid)`
  and `count(*)` over `lexeme_occurrences`) full-scanned the 17.5M-row table,
  costing ~18.7 s of origin time against a 300 ms SLA (confirmed live via
  `EXPLAIN ANALYZE` as `docray_api` + admin principal). Migration 095 adds two
  precomputed rollups read on the hot path: `document_stats` (per-document
  sentence/occurrence counts, RLS-scoped so `SUM(...)` over the caller's visible
  documents stays exact per principal, ~2 ms) and `corpus_stats` (the
  non-summable distinct-lexeme count as a maintained **per-tenant** scalar,
  visibility-gated to 0 for a principal with no visible corpus). The scalar is
  keyed per tenant — never one global row — so it cannot leak one tenant's
  vocabulary magnitude to another. Rollups refresh in the trusted batch writer
  (ingest/annotate), never on the read path; the per-tenant scalar is
  debounced (default 10 min) so a bulk re-annotation pays the recompute once, not
  once per document.

## [1.30.0] - 2026-07-16

### Added
- **Prism Tier-3 grounded VLM captions — gate + schema** (Claude Opus 4.8 via
  Claude Code). The specificity-weighted grounding scorer
  (`application/visual_grounding.py`) — the PALS gate that caps generic captions
  so they route to `visual_review` instead of the trusted graph — plus the
  `visual_caption` and (net-new-RLS) `visual_review` tables (migrations 093-094)
  and a corpus-wide vision-backfill enqueuer (`application/visual_backfill.py` +
  `scripts/enqueue_visual_backfill.py`).

## [1.29.0] - 2026-07-16

### Added
- **Prism Tier-2 visual layer — vision embeddings (schema + port)** (Claude Opus
  4.8 via Claude Code). HNSW-indexed `image_embedding` and (real text-tower)
  `sentence_vision_embedding` tables under the existing RLS (migrations 091-092),
  and a `VisionEncoder` port (dual image/text encoder) so cross-modal retrieval
  is independent of the GPU serving stack. Vectors rank only; they never assert a
  fact into the trusted graph.

## [1.28.0] - 2026-07-15

### Added
- **Prism Tier-1 visual layer — deterministic image features** (Claude Opus 4.8
  via Claude Code). First tier of the visual/multimodal layer (see
  `docs/proposals/prism-visual-layer.md`): two additive, document-scoped tables
  (`document_image`, `image_tier1_features`) under the existing RLS, and a pure
  deterministic feature extractor (`application/visual_features.py`) — colour
  palette (median-cut, byte-reproducible), per-channel histogram, saturation/
  value/contrast/edge-density, and a rule-based image-type class. No model, no
  hallucination surface: this tier is ground truth. Migrations 089–090.

## [1.27.0] - 2026-07-15

Schema 1.27.0 (migrations 085-086).

### Fixed
- **Billing tenant identity is deploy-safe** (OpenAI GPT-5 via Codex) -
  GraphQL billing status now returns the real tenant slug/display name in the
  admin console, and a forward repair migration grants the client runtime role
  tenant catalogue read access on deployments that had already recorded the
  tenant-foundation migration.

## [1.26.0] - 2026-07-15

Schema 1.26.0 (migrations 083-084).

### Added
- **Billing payment activity** (OpenAI GPT-5 via Codex) - tenant admins can
  inspect recent Stripe webhook events in GraphQL and the admin-console Billing
  panel, with RLS-backed filtering by tenant/customer and regression coverage
  for event visibility.

## [1.25.0] - 2026-07-15

Schema 1.25.0 (migrations 080–081).

### Added
- **Stripe subscription billing foundation** (OpenAI GPT-5 via Codex) —
  tenant-scoped billing account/subscription tables, signed Stripe webhook
  event processing, hosted Checkout and Customer Portal GraphQL mutations,
  and an admin-console Billing panel wired to the live billing status/actions.
  Regression coverage exercises webhook signature rejection, idempotent
  subscription event processing, Checkout customer/session creation, and the
  console GraphQL/page contract.

## [1.24.0] - 2026-07-13

Schema 1.24.0 (migrations 076–077).

### Fixed
- **The four MAJOR local-auth audit findings** (Claude Fable 5 via Claude
  Code, TDD) — brute-force lockout (`DOCRAY_AUTH_MAX_FAILED_LOGINS` /
  `DOCRAY_AUTH_LOCKOUT_SECONDS`) plus `/login`–`/register` rate limits with
  no account-state oracle; refresh-token rotation state persisted in
  `auth_refresh_tokens` (previously process-memory only, so every API
  restart revoked all sessions); role-based corpus read visibility so a
  fresh reader sees the tenant corpus except `sensitivity=sensitive`
  documents, which stay ACL-gated (migration 076); and webapp→API writes now
  act as the logged-in principal via an explicit `impersonate`-scoped
  `X-DocRay-Act-As` delegation instead of riding the admin service token.
- **Verification-delivery blocker** (Claude Fable 5 via Claude Code) — a
  registered account could never verify: the raw token was discarded and no
  mail sender existed anywhere in the repo. New `Mailer` port + stdlib SMTP
  adapter (`DOCRAY_SMTP_*`, STARTTLS, optional AUTH), fail-closed
  registration when delivery is unavailable, and a resend route.
- **Browser corpus access requires web auth** (fleet agent) — webapp read
  routes no longer serve anonymous sessions.

## [1.23.0] - 2026-07-12

Schema 1.23.0 (migrations 074–075).

### Added
- **Local self-service auth** (fleet agent) — Argon2id credential storage
  (`principal_credentials`), email-verification tokens, and webapp
  registration/login for reader-role principals (migrations 074–075).
- **Document collections exposed in GraphQL** (fleet agent).
- **`pipeline_health_check`** (Claude Fable 5 via Claude Code) — one-shot
  broken-state report for the pipeline.
- **Short-job lane: kind-filtered claiming + reserved `worker-fast` service**
  (Claude Fable 5 via Claude Code) — the 2026-07-12 starvation incident's
  final follow-up: with every worker chewing a book-sized reannotate, a
  seconds-long ingest sat `Queued` for 25+ minutes with no worker available.
  `queue_postgres.claim()` (and `run_once`) now accept `only_kinds`/
  `exclude_kinds`; the worker reads `WORKER_ONLY_KINDS`/`WORKER_EXCLUDE_KINDS`
  (comma-separated) and passes them to every claim. A new compose
  `worker-fast` service runs with `WORKER_EXCLUDE_KINDS=annotate,reannotate`
  (override via `WORKER_FAST_EXCLUDE_KINDS`) as reserved capacity for
  user-facing jobs — ingest, metadata edits, deletes, backups — so they never
  queue behind hours-long spaCy runs. Jobs a filtered worker skips stay
  queued for its peers; unfiltered workers are unchanged. Regression tests
  cover exclude/only/unfiltered claiming and `run_once` threading.

### Changed
- **Hot paths stop per-row `substring(full_text)` detoasting**
  (Claude Fable 5 via Claude Code) — enrichment and webapp queries no longer
  detoast the full document text per row.

### Fixed
- **Language detection ignores single-letter tokens** (Claude Fable 5 via
  Claude Code).
- **Honest pipeline-step status in the webapp** (Claude Fable 5 via Claude
  Code) — failure banners, elapsed time, no fabricated ETA.
- **Pipeline polish pass** (Claude Fable 5 via Claude Code) — BL-134, four
  root-cause fixes from the 2026-07-12 incident review, each with a
  regression test:
  - *Janitor reap thread*: `reap_stale` only ran between jobs, so with every
    worker busy inside a long handler an orphaned job waited for a free slot
    before recovery (observed live: 45+ minutes). A daemon thread now reaps
    every `WORKER_REAP_SECONDS` (default 30) on fresh short-lived
    connections, surviving any tick error.
  - *Attempt-relative `started_at`*: claiming preserved the first attempt's
    start, so a retry's ETA/runtime read hours of elapsed time at zero
    progress ("~8m left" seconds into a fresh attempt). Claim now stamps
    `started_at = now()`.
  - *Progress hygiene on reap*: released and crash-requeued rows clear
    `progress_current/total/stage`, so queued jobs no longer display a dead
    attempt's percentage while waiting (terminally failed rows keep theirs
    for forensics).
  - *Layout fetch without correlated subqueries*: the staged-span filter
    (JOIN, later EXISTS) against the unindexed `_span_stage` temp table
    degenerates to an O(rows x spans) nested loop in the planner; the
    geometry pass is log-linear and deduplicating since BL-133, so the
    document's runs are fetched once, bounded by document size.
- **Span-geometry backfill is no longer quadratic** (Claude Fable 5 via
  Claude Code) — BL-133, the root cause behind the 2026-07-12
  "Registering N annotation spans" stalls and the execution-timeout failure:
  `span_geometry_from_layout_rows` scanned every layout row for every span
  (171k spans × a 303-page book's rows ≈ tens of billions of pure-Python
  iterations), and `_update_span_geometry_from_layout`'s JOIN emitted one
  duplicate copy of each layout row per overlapping staged span — inflating
  the row list and skewing the per-page overlap vote. Rows are now
  deduplicated, sorted once, and bisected to a per-span candidate window
  (O((n+m) log m)); the caller uses EXISTS. Regression tests pin the
  duplicate-vote semantics, equivalence with the original scan, and a
  12k×12k wall-clock budget (44.6s → 0.1s).

## [1.22.0] - 2026-07-12

### Added
- **Deploy-safe drain release for the job queue** (Claude Fable 5) — a worker
  that receives SIGTERM now stamps its in-flight job's new
  `jobs.drain_requested_at` column from the heartbeat thread (migrations 072,
  073). When the process is SIGKILLed before the handler finishes (deploy
  stop-grace expiry), the reaper releases the stamped job back to the queue
  with the claim-time attempt refunded — `error` reads
  `released: worker shutdown during drain` — instead of burning one of
  `max_attempts` per deploy. A crash without a drain stamp still spends its
  attempt, preserving the poison-message bound. Long annotate jobs can no
  longer be permanently failed by consecutive deploys alone.

### Changed
- **Annotation jobs get a 4-hour execution budget** (Claude Fable 5) — the
  compose worker now sets `WORKER_JOB_TIMEOUT_ANNOTATE_SECONDS` and
  `WORKER_JOB_TIMEOUT_REANNOTATE_SECONDS` to 14400 by default: a
  15.5k-sentence book was terminally failed at exactly the 3600s default
  while making steady progress. Other job kinds keep the 1h default.

### Fixed
- **Retries no longer display the dead attempt's progress** (Claude Fable 5) —
  claiming a job now clears `progress_current`/`progress_total`/
  `progress_stage` (and any stale drain stamp), so a reclaimed job's second
  attempt starts from a clean progress row instead of appearing frozen at the
  previous attempt's percentage.

## [1.21.0] - 2026-07-12

### Added
- **Operator job-spool visibility** (Codex GPT-5) — the restricted operator
  health page and JSON contract now expose queued/running/failed/reclaimed
  jobs with job id, kind/status, attempts, worker, document, progress stage,
  runtime, heartbeat age, queued age, reclaim countdown, and last error. This
  turns stalled ingest/annotate work from an invisible background condition
  into an inspectable operator surface.
- **Worker lifecycle logging** (Codex GPT-5) — workers now log claim, no-work,
  poison-message rejection, completion, lost-claim, cancel-before-completion,
  and retry/terminal failure paths with worker id, job id, kind, document id,
  attempt count, request id, and retry outcome.
- **Persistent worker-scaling Makefile path** (Codex GPT-5) — `make
  workers-up` defaults to three workers through `DOCRAY_WORKERS ?= 3`, and
  `make workers-status` exposes the worker process state without requiring
  operators to remember the raw Compose `--scale` invocation.

### Changed
- **Annotation progress is finer-grained** (Codex GPT-5) —
  `SpacyAnnotator.annotate` now reports a stable 12-step scale, separating
  token span collection, entity/chunk span collection, span registration with
  a count, row-building substeps, lexeme projection, validation, persistence
  start, and persistence completion. Long annotation jobs no longer sit on one
  opaque span-registration phase.

### Fixed
- **Collections actually persist** (Claude Fable 5 via Claude Code) — BL-131,
  from an operator screenshot ("Collection not found" right after creating
  one). The BL-057 collection routes write directly through the webapp's
  request connection; `get_conn()` opened psycopg without autocommit, and
  request teardown closed the connection — rolling back every collection
  create and member add/remove *after* the redirect had been issued from
  inside the doomed transaction. The request connection is now
  `autocommit=True` (matching every other connection in the codebase). The
  regression test drives the real two-request lifecycle and was verified to
  fail with the fix reverted — the suite's `client` fixture fakes `q`/`q1`
  wholesale, which is why the original tests could not see this.
- **auth_admin CLI operator UX** (Claude Fable 5 via Claude Code) — BL-128,
  from an operator traceback report:
  - An unreachable Postgres now exits 2 with an actionable, password-redacted
    message (points at `set -a; source .env; set +a` and `--database-url`)
    instead of a raw psycopg traceback.
  - `doc_ray.config.DEFAULT_DATABASE_URL` is the single host-side fallback
    (factory port 5432); `webapp/app.py` and the test harness previously
    hardcoded a divergent `localhost:5455`. A regression test fails on any
    reintroduced divergent literal.
  - README gains a copy-paste-safe `principal create` / `token mint`
    quickstart (shell-variable placeholders, `.env` sourcing first) — the
    workflow previously had no operator docs at all.
- **GraphQL lexeme vocabulary visibility** (Codex GPT-5) — BL-127 makes
  `lexemes` and `lexeme(uid:)` require at least one RLS-visible occurrence, so
  anonymous callers can no longer enumerate vocabulary derived only from hidden
  documents; the webapp lexeme index now follows the same visible-occurrence
  rule.
- **Job progress that actually advances, ETA, stalled-job recovery, and a
  guarded corpus-truncate task** (Claude Fable 5 via Claude Code) — closes
  the gaps found verifying BL-123's first landing against the operator's
  bar ("no status, no progress, no ETA, no %"):
  - The progress reporter moved to `domain/progress.py` (contextvar), so
    adapters can report without breaking the hexagonal import direction
    (guarded by a new AST test); `application.jobs` re-exports it unchanged.
  - `SpacyAnnotator.annotate` now reports advancing phase boundaries instead
    of a frozen `0/1`; deterministic semantic enrichment reports its five
    layers. A 248-page annotate now shows an advancing bar with stage text
    rather than a spinner-equivalent.
  - `webapp/job_view.summarize_progress` derives a naive linear **ETA**
    from `started_at` once progress advances ("~3m left"), rendered next to
    the percent in the pipeline panel and job page.
  - A running job whose heartbeat went stale (worker killed mid-run by a
    deploy — Docker's default 10s stop grace) now reads as **"Stalled
    (worker lost)"** and no longer blocks the BL-119 reprocess buttons;
    the compose worker gets `stop_grace_period: 120s` so drains can finish.
    Root cause of tonight's stuck annotate: repeated deploy restarts killed
    attempt after attempt, exhausting retries while the row wore `running`.
  - New `make truncate-db CONFIRM=yes` (first Makefile) wipes the corpus
    for fresh uploads behind a fail-closed guard and an automatic
    `make db-dump` safety backup; `scripts/truncate_corpus.py` preserves
    schema/auth/canonical-registry/backups, with live-schema tests forcing
    a wipe-or-preserve decision for any future table.
- **Duplicate upload Content-Type preservation** (Codex GPT-5) — BL-095 makes
  `ingestDocument` use the extractor-owned content type for its pre-worker blob
  upload, so re-uploading an already-ingested file no longer downgrades object
  metadata to `application/octet-stream`.

### Testing
- **Extraction format coverage guard** (Codex GPT-5) — BL-093 adds a
  golden-case drift guard for every registered extractor extension, so future
  PPTX/SVG registrations fail tests until they ship with format coverage.

## [1.20.0] - 2026-07-12

### Added
- **Per-page PDF geometry persistence** (Codex GPT-5) — BL-126 adds
  `document_pages`, persists every extracted PDF page's dimensions even when no
  text was found, refreshes those rows during re-extraction, and lets the
  original-page overlay render text-free pages without pretending the PDF needs
  another extraction.

## [1.19.0] - 2026-07-12

### Added
- **Tenant-scoped SAML identity-provider configuration** (Codex GPT-5) —
  BL-067 adds `tenant_saml_identity_providers`, letting each tenant configure
  its SAML entity, metadata, service-provider entity, ACS URL, signature
  policy, and JIT provisioning posture without sharing IdP state across
  tenants.

## [1.18.0] - 2026-07-12

### Added
- **Tenant foundation, security audit events, ABAC hooks, and SCIM
  provisioning** (Codex GPT-5) — BL-064/BL-065/BL-068 add tenant ownership for
  principals/documents, append-only security audit events with admin readback,
  sensitive-document write checks, and SCIM-managed principal/group state.

## [1.17.0] - 2026-07-12

### Added
- **First-class async job progress** (Codex GPT-5) — BL-123 adds
  `progress_current`, `progress_total`, and `progress_stage` to jobs; exposes
  progress through the queue adapter, GraphQL `Job`, SSE job events, worker
  handlers, and the webapp pipeline status views.

## [1.16.0] - 2026-07-12

### Added
- **Admin job cancellation** (Codex GPT-5) — BL-102 adds an admin-gated
  `cancelJob` GraphQL mutation for queued/running jobs, a Postgres queue cancel
  transition, and a worker completion checkpoint so a job canceled during handler
  execution stops without being marked succeeded or advancing its chain.

## [1.15.0] - 2026-07-12

### Added
- **Verified document metadata suggestions** (Codex GPT-5) — BL-061 adds
  human-reviewed title, tag, and duplicate suggestions. Suggestions are
  generated by a background `suggest_document_metadata` job, pass the shared
  model-output verification port before persistence, surface confidence bands
  in GraphQL and the document page, and are never auto-applied.

## [1.14.0] - 2026-07-12

### Added
- **Document lifecycle states** (Codex GPT-5) — BL-059 adds a reversible
  document lifecycle separate from ingestion `status`: `active`, `archived`,
  and soft-deleted `deleted` documents with `archived_at`/`deleted_at`
  timestamps. `deleteDocument` now enqueues a soft delete, and new
  `archiveDocument` / `restoreDocument` mutations enqueue per-document archive
  and restore jobs. Lifecycle transitions are written to
  `document_metadata_history`.

## [1.13.0] - 2026-07-12

### Added
- **Span page geometry persistence** (Codex GPT-5) — BL-077 adds nullable
  page/bbox fields on `spans` plus a document-scoped `document_layout_runs`
  index so sentence, token, entity, and chunk spans created after ingestion can
  inherit PDF page geometry. Migration `049` adds the schema; migration `050`
  advances the unified package/schema version to `1.13.0`.

## [1.12.0] - 2026-07-12

### Added
- **Document collections** (Codex GPT-5) — BL-057 adds manual and smart
  document collections. Migration `047` introduces `document_collections` and
  document-scoped `document_collection_members`; migration `048` advances the
  unified package/schema version to `1.12.0`. The webapp now has collection
  listing, creation, detail, and add/remove flows, with smart collections
  resolving doc_type/status/tag rules dynamically.

### Security
- **BL-110 follow-through: the two resolvers the original sweep missed now
  validate ids** (Claude Fable 5 via Claude Code) — a backlog-vs-code audit
  found `documentMetadataHistory` still passed its `documentId` unvalidated
  into the uuid-typed history query (a malformed id surfaced Postgres's raw
  `invalid input syntax for type uuid` text to any caller), and
  `updateDocumentMetadata` enqueued with only global write scope and no id
  validation, unlike every sibling document-scoped mutation. The query now
  short-circuits to an empty page via `_clean_uuid`; the mutation now goes
  through `_require_document_write` (per-document grant check + id
  validation, BL-064's pattern). Regression tests cover both.
- **Container & dependency supply-chain hardening** (Claude Fable 5 via
  Claude Code) — BL-111, BL-112, BL-114 as one lane; see engineering decision
  `D-024`:
  - The app `Dockerfile` is now two stages, both pinning
    `python:3.12-slim` by `@sha256:` index digest (BL-111 — the same
    reproducibility argument BL-039 settled for Postgres, which its
    regression test simply didn't cover for the app's own image). A new
    `test_app_base_image_is_pinned_to_an_immutable_digest` guards every
    `FROM` line, so a future multi-stage addition can't quietly float.
  - Every service built from the image now runs as system user `docray`
    (uid 10001, no login shell) instead of root (BL-112). `/app` and
    site-packages stay root-owned and read-only to the app; runtime writes
    go to tempdirs or `$HOME`; `/certs` is pre-owned so the `nginx-certgen`
    one-shot bootstraps the `nginxcerts` volume unprivileged. Verified live
    on an isolated compose stack: every app service healthy as uid 10001,
    with a full ingest→annotate→semantic_enrich→canonicalize pipeline and a
    scheduled backup completing end-to-end non-root.
  - The committed `uv.lock` is now enforced instead of decorative (BL-114):
    the Dockerfile's deps stage installs the runtime set with
    `uv sync --locked --no-install-project --no-dev` (build fails on lock
    drift; uv binary itself digest-pinned; hash-verified installs, including
    the direct-URL spaCy model), both CI jobs install the same way, and the
    static job gained an explicit `uv lock --check` drift gate.
    `requirements-dev.txt` remains the (glob-pinned) source for dev tooling
    only. New `tests/test_uv_lock_sync.py` enforces lock consistency and
    the no-plain-pip invariant in both surfaces, with self-tests proving
    the checker can fail.
- **CSRF protection on every state-changing webapp form** (Claude Sonnet 5 via
  Claude Code) — upload, backup create/restore, and seeding approve/reject/seed
  were all plain POST forms with no CSRF token, no SameSite reliance
  documented, and no Origin check anywhere in `app.py`; a logged-in operator's
  browser could be tricked into submitting any of these from another origin.
  A per-session token (session-cookie-backed, compared with
  `hmac.compare_digest`) is now minted via a Jinja `csrf_token()` global and
  verified in a `before_request` hook that rejects every
  POST/PUT/PATCH/DELETE without a matching token before the route body runs —
  one hook covers all four mutating routes (and any future one) without a
  per-route opt-in, since every webapp mutation is a same-origin `<form>`
  post (no JS fetch/XHR writes exist), so no new dependency was needed. New
  `DOCRAY_WEBAPP_SECRET_KEY` signs the session cookie. Closes `BL-103`.
- **GraphQL introspection/GraphiQL gated; ingestDocument upload size capped;
  EPUB/DOCX zip-bomb + entity-expansion hardening** (Claude Sonnet 5 via
  Claude Code) — three findings from the 2026-07-10 full-codebase gap
  analysis (BL-108, BL-098, BL-088), following BL-094/BL-064:
  - `entrypoints/api/schema.py`'s `build_schema` disables introspection
    (Strawberry's `DisableIntrospection`) unless the new `DOCRAY_API_DEBUG`
    opts in; `entrypoints/api/app.py` gates the GraphiQL IDE the same way
    (`graphql_ide=None` outside debug, so it 404s). Previously an anonymous
    caller (the default) could enumerate the entire schema, including
    admin-only mutation argument shapes, for free reconnaissance.
  - `ingestDocument` now reads its upload in bounded chunks against a new
    `Config.max_upload_bytes` (`DOCRAY_MAX_UPLOAD_MB` — the same knob the
    webapp's Flask route already enforced), rejecting an oversized upload
    before the full body is ever buffered in API process memory. Previously
    unbounded.
  - `adapters/extraction.py`'s XML parser (`ET`) now aliases
    `defusedxml.ElementTree` instead of stdlib, rejecting DOCTYPE/entity
    payloads outright; a new `_check_zip_bomb_bounds` helper (configurable
    via `DOCRAY_EXTRACT_MAX_UNCOMPRESSED_MB`, default 500 MiB) rejects a
    zip whose declared uncompressed size is disproportionate to its
    compressed size before `extract_epub`/`extract_docx` read any member.
    Previously EPUB/DOCX had neither guard.
- **Worker DB-reconnect resilience; GraphQL id-argument validation; mutation
  batch bounds** (Claude Sonnet 5 via Claude Code) — three more findings from
  the 2026-07-10 full-codebase gap analysis (BL-101, BL-110, BL-109). See
  engineering decision `D-020`:
  - `entrypoints/worker.py`'s outer poll loop now catches
    `psycopg.OperationalError` from the reap/claim cycle, backs off
    (`WORKER_RECONNECT_BACKOFF_SECONDS`, default 2s), and reconnects, instead
    of a dropped connection crashing the whole worker process.
  - `entrypoints/api/query.py`'s `document`/`job`/`searchSentences`/
    `lexemes`/`entities`/`entityFrequency`/`jobs`/`seedSuggestions`/
    `unresolvedMentions`/`resolutionReview` resolvers now validate any
    uuid-shaped id argument (a shared `_clean_uuid()`) before querying; a
    malformed id now reads as "not found"/an empty page instead of
    forwarding Postgres's raw `invalid input syntax for type uuid` text.
  - `suggestSeeds`'s `limit` is now clamped through the same `page_size()`
    bound every read resolver uses; `decideSeeds`'s `approveIds`/
    `rejectIds` are rejected outright (new `context.MAX_BATCH_IDS`, 500) —
    not silently truncated — when a batch is too large.
- **Webapp security headers; upload-extension allowlisting; header-based
  operator token** (Claude Sonnet 5 via Claude Code) — BL-104, BL-106,
  BL-107. See engineering decision `D-021`:
  - New `webapp/security_headers.py` sets `Content-Security-Policy`,
    `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and
    `Referrer-Policy: strict-origin-when-cross-origin` on every response,
    via `app.after_request` — no new dependency. Previously none of these
    headers were set anywhere.
  - `ingestDocument` now checks the upload extension against
    `extraction.is_supported_extension` (the same allowlist `extract()`
    dispatches on) before the blob ever reaches the object store, not
    after. Previously the attacker-supplied extension was persisted first,
    validated only later during the worker's ingest job.
  - `operator_health.py`'s `require_operator_health` accepts the
    `X-Operator-Token` header standalone; a query-string `?token=`
    bootstrap link now sets a signed session flag so later navigation no
    longer needs the token in the URL. `operator_health.html`'s internal
    link no longer embeds the raw token.
- **Structured logging for the worker and API processes** (Claude Sonnet 5
  via Claude Code) — BL-115. See engineering decision `D-022`: new
  `doc_ray.logging_setup.configure_logging()` (level/format via
  `DOCRAY_LOG_LEVEL`/`DOCRAY_LOG_FORMAT`) is called at startup by
  `entrypoints.worker.main` and `entrypoints.api.app.create_app`. Worker
  job failures now log the full traceback plus job id/kind/document_id
  (`application/jobs.py`), not just `repr(exc)` tucked into the `jobs.error`
  column; the worker also logs a warning on the BL-101 reconnect path.
  GraphQL resolver exceptions needed no code change — Strawberry's default
  `process_errors` already logs them to `strawberry.execution`; the gap was
  that nothing configured logging at all, so that call fell through to
  Python's unconfigured handler of last resort.

### Added
- **Documentation drift gates: BL-048 landed, BL-047 completed** (Claude
  Fable 5 via Claude Code) — new `tests/test_docs_in_sync.py` asserts
  DATABASE.md's "N migrations applied" header equals the `db/migrations/*.sql`
  file count, that its §9 migration-history table lists every migration file,
  and that the BL-047 drift gate stays wired into pre-commit and CI. Its first
  run caught real drift: `scripts/_data_dictionary_content.py`'s hand-curated
  `MIGRATIONS` list had silently stopped at 037, so DATABASE.md's history
  table was missing 038–046 — list extended, file regenerated. The BL-047
  `--check` gate now also runs as a pre-commit hook
  (`gen_data_dictionary.py --check --skip-without-db`, a new flag that prints
  an explicit SKIPPED notice instead of a stacktrace when no database is
  reachable; CI keeps running the strict form).
- **nginx TLS front door for the webapp** (Claude Fable 5 via Claude Code) —
  new compose `nginx` service (nginx:1.27-alpine) serves the webapp over
  HTTPS on `${NGINX_HTTPS_PORT:-443}` and 301-redirects plain HTTP on
  `${NGINX_HTTP_PORT:-80}` (host/path/query preserved). A one-shot
  `nginx-certgen` service (createbucket pattern, stack image's openssl)
  bootstraps a self-signed certificate (CN=localhost, SAN
  localhost/127.0.0.1) into the new `nginxcerts` volume on first start and
  never overwrites existing certs, so real certificates can be dropped in.
  Config ships as `nginx/templates/default.conf.template`, rendered over the
  image's stock config by its envsubst hook; `client_max_body_size` is wired
  to `${DOCRAY_MAX_UPLOAD_MB:-200}m` so nginx's 1 MiB default doesn't 413
  uploads the webapp/API would accept. No HSTS on purpose while the default
  cert is self-signed (it would remove the browser's cert-warning bypass);
  plain-HTTP `/healthz` is exempt from the redirect and backs the compose
  healthcheck. nginx binds all interfaces as the intentional entry point —
  the webapp itself stays loopback-only — but the UI still has no login, so
  the exposed ports must stay inside a trusted network.
- **Webapp joins the compose stack as a first-class service** (Claude Fable 5
  via Claude Code) — `docker compose up -d webapp` now serves the Flask corpus
  browser at `http://127.0.0.1:${WEBAPP_PORT:-5001}`, wired to the stack's
  Postgres and to the api service's in-network GraphQL endpoint
  (`http://api:8000/graphql` — hard-coded like `docker-compose.api.yml`'s
  `S3_ENDPOINT`, so a host-oriented `.env` URL can't dead-end inside the
  container). The shared `Dockerfile` now ships `webapp/`. The container runs
  `flask run` (debug off) rather than `python -m webapp.app`, whose
  `debug=True` would expose the Werkzeug debugger; the host port binds to
  127.0.0.1 because the UI has no login and `/backups` can restore over the
  live database. Healthcheck probes `/`, which doubles as a DB-reachability
  check. `DOCRAY_WEBAPP_SECRET_KEY`, `DOCRAY_WEBAPP_API_TOKEN`,
  `DOCRAY_OPERATOR_HEALTH_TOKEN`, `DOCRAY_MAX_UPLOAD_MB`, `DOCRAY_DB_ROLE`,
  and `DOCRAY_PRINCIPAL_ID` pass through from `.env`.
- **Doc-hygiene audit fixes + drift regression tests** (Claude Sonnet 5 via
  Claude Code) — README's quick-start referenced a removed fixture
  (`orwell1984.pdf`) and its GraphQL surface list had drifted 7 fields behind
  the live schema; `.env.example` was missing ~20 environment variables the
  code actually reads (`API_MAX_DEPTH`/`API_MAX_TOKENS`, `WORKER_*`,
  `DOCRAY_PDF_HEADING_*`, `DOCRAY_PDF_OCR*`, `PORT`, `S3_REGION`, and others);
  `CLAUDE.md` still carried its unfilled seed-template block and an unresolved
  `PURPOSE.md` reference. `docs/DATABASE.md` is regenerated (schema `1.9.0`,
  42 migrations). New `src/doc_ray/tests/test_doc_hygiene.py` makes each of
  these a mechanical drift guard going forward (each with a self-test proving
  it can actually fail). `docs/doc-ray-database-*.pdf/svg` ERD diagrams remain
  stale — no generator script exists for them yet (tracked as a gap, not fixed
  here; see the doc-hygiene report's Recommendation 1).
- **PDF extraction surfaces per-line page number + bounding box** (Claude
  Sonnet 5 via Claude Code) — BL-076, the foundational first step for a
  future overlay-on-original-document feature (see engineering decision
  `D-023`). `extract_pdf_pymupdf` now attaches a new `Extracted.page_runs`
  field (`None` for every non-PDF-pymupdf extractor): one
  `{page, bbox, text}` entry per line, computed by the new
  `domain.pdf_layout.page_runs_from_blocks`, a sibling pass over the same
  `blocks` structure `to_markdown` already consumes. `full_text`/
  `to_markdown`'s output is unchanged — this only surfaces geometry that was
  already being computed and discarded.
- **Semantic enrichment joins the default pipeline; entity/chunk reads stop
  rejoining `document_content`** (Claude Sonnet 5 via Claude Code) — the
  post-ingest pipeline chain is now `ingest -> annotate -> semantic_enrich ->
  canonicalize`, queueable explicitly via the new GraphQL
  `semanticEnrichDocument` mutation or the `semantic_enrich` job kind.
  Separately, `entity_mention` and `chunk` each gain a denormalized `surface`
  column (migrations 038-042, schema `1.9.0`), populated at annotation write
  time; `entityFrequency`, `entities`, `chunks`, `entityLinks`, and
  `unresolvedMentions` read it directly instead of recovering the text via a
  per-row `substring()` join against `document_content` at query time. That
  join, combined with a `GROUP BY` blocking `LIMIT` pushdown, had let an
  unremarkable `entityFrequency`/`entities` call (no `documentId` filter) force
  an unbounded, TOAST-detoasting scan across the whole corpus — 7-17 minutes
  pinning every core across 8 parallel backends. Every pooled API connection
  now also carries a Postgres `statement_timeout`
  (`DOCRAY_API_STATEMENT_TIMEOUT_MS`, default 15s) as a second, independent
  guardrail against any future unbounded query. See `engineering_decisions.yml`
  `D-017`.
- **Webapp: shared stylesheet, graceful database-unavailable page**
  (Claude Sonnet 5 via Claude Code) — the design tokens and component styles
  that `base.html` inlined, plus several near-duplicate per-page rules
  (`.hint`/`.muted`, `.notice`/`.errbox`, the job/seeding status pill, the
  dense-table `th`/`td` rules), are consolidated into
  `webapp/static/design-system.css`; no visual change. A Postgres connection
  failure (unreachable, or a rejected password) now renders a plain "Can't
  reach the database" page with the connection error and a short checklist,
  instead of surfacing Werkzeug's raw interactive debugger/traceback.
- **Drift-gate for the generated data dictionary + hexagonal import guard**
  (Claude Opus 4.8 via Claude Code) — `scripts/gen_data_dictionary.py` gains a
  `--check` mode (`_in_sync`) that fails when `docs/DATABASE.md` is stale versus the
  live schema, wired as a CI gate that runs after migrations; a new
  `src/doc_ray/tests/test_import_boundary.py` fails if `doc_ray.domain` or
  `doc_ray.ports` import an outer layer, enforcing the points-inward dependency rule
  by test. The generator's hand-authored data constants (`RLS_PATH`, `MIGRATIONS`)
  move to `scripts/_data_dictionary_content.py` to keep both files within the 400
  net-LOC cap (D-015). Closes `BL-047` and `BL-052`.
- **Evaluation, semantic enrichment, and math extraction completion** (OpenAI GPT-5 via Codex) —
  migration 036 adds durable `corpus_snapshot`, `evaluation_run`,
  `evaluation_metric`, and `gold_sample` artifacts, while
  `application.evaluation` records lexical/entity distributions with citeable
  fact references and deterministic snapshot deltas. PDFs can opt into a
  verified external math extractor contract (`DOCRAY_PDF_MATH_BACKEND=external`)
  that rejects non-LaTeX ML output before persistence. The new
  `annotate_semantic` entrypoint fills deterministic discourse, temporal/event,
  sentiment, and hash-BoW embedding layers, closing `BL-002`, `BL-006`, and
  `BL-024`.
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
- **Backlog descriptions reconciled with the code** (Claude Fable 5 via
  Claude Code) — full 118-item audit of `docs/backlog.yml` against the
  implementation. `BL-048` parked→done (see Added); stale or false claims
  corrected in `BL-012` (impersonation gap is closed, not "still open"),
  `BL-051` (no MCP entrypoint exists), `BL-069` (authenticator is wired in
  `app.py`, not `config.py`), `BL-070` (SECRET_KEY/session now exist for
  CSRF and the operator-health gate — just not for identity), `BL-111`
  (test path), `BL-116` (logger call-site count); rotted line-number
  citations in `BL-080`/`BL-084`/`BL-086`/`BL-091`/`BL-092` replaced with
  stable symbol names.
- **deleteDocument no longer leaks the document's blob in object storage**
  (Claude Sonnet 5 via Claude Code) — `handle_delete` (`application/jobs.py`)
  previously only issued `DELETE FROM documents`; a repo-wide grep for
  `store.delete(` showed exactly one call site in all of `src/doc_ray`
  (`application/backup.py`, pruning old backup archives), so every document
  delete leaked its original bytes in MinIO/S3 forever. `handle_delete` now
  reads `source_uri` before the row is removed, then deletes the matching
  object-store key after the DB delete succeeds — tolerating a document with
  no configured store or a `source_uri` outside the configured bucket.
  Closes `BL-097`.
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

## [1.11.0] - 2026-07-10

### Added
- **Editable document metadata (title/description/tags) with a change-history
  audit trail** (Claude Sonnet 5 via Claude Code, BL-058) — documents were
  write-once at ingest: no mutation or webapp route could rename a document
  or fix a wrong title, add a description, or correct a tag afterward.
  `documents` gains a `description` column and a new append-only
  `document_metadata_history` table (migration 045), RLS-scoped and
  read-granted like `document_provenance`; the `updateDocumentMetadata`
  GraphQL mutation enqueues an `update_metadata` job that applies the edit
  and records one history row per changed field. The webapp's document page
  gains an edit form and a change-history view. Schema `1.11.0`.
- **Document list pagination + query filters** (Claude Sonnet 5 via Claude
  Code, BL-063) — the corpus landing page fetched every document unbounded
  and `/search` hard-capped at 100 results with no way to see more. Both now
  page with a prev/next pager, matching the webapp's own established
  convention (`lexemes()`, the `/doc/<id>` sentence reader). The `documents`
  GraphQL query and `persistence_read.list_documents` gained `doc_type`/
  `status`/`tags` filter arguments alongside the existing keyset cursor.

Merging BL-058/BL-063 (developed in a separate worktree) into `main` surfaced
and fixed three gaps: the branch's own `043`/`044` migrations collided with
`main`'s BL-064 migrations of the same numbers (both landed independently
after a shared ancestor) — resolved by renumbering the branch's migrations to
`045`/`046` and the schema bump to `1.11.0` rather than re-using `1.10.0`; the
new metadata-edit form (`document.html`) predated `main`'s BL-103 CSRF
protection and had no hidden `csrf_token` field, so it would have been the
one unprotected mutating form in the webapp — fixed before landing; and the
`document_metadata_history` table was never added to
`scripts/gen_data_dictionary.py`'s curated table list, so `docs/DATABASE.md`
would have silently stayed incomplete — fixed. `webapp/app.py` also crossed
the D-015 400-net-LOC cap after the merge; `webapp/csrf.py`,
`webapp/document_metadata.py`, and `webapp/graphql_client.py` split out the
CSRF hook, the metadata-update mutation builder, and the GraphQL write-path
client respectively, and `DocumentMetadataChange` moved to a new
`entrypoints/api/audit_types.py`, matching the project's existing
type-file-by-concern split.

## [1.10.0] - 2026-07-05

### Security
- **Mutation resolvers never checked per-document write ACL** (Claude Sonnet 5
  via Claude Code) — every GraphQL mutation checked only a global "write"
  scope (`_require_write`), never the caller's `document_acl` grant for the
  specific `document_id` being mutated; `jobs` and `backups` had no RLS at
  all (only a direct `GRANT` to `docray_client`). Net effect: any token with
  global write scope could enqueue `reannotate`/`delete`/`canonicalize`/
  `restore` against **any** document in the corpus, not just the ones it was
  granted. Migration 043 enables RLS on `jobs` (document-scoped reads/writes
  follow the caller's existing grants; corpus-wide jobs like backup/restore
  require admin) and `backups` (admin-only, corpus-wide by nature); a new
  `_require_document_write`/`_require_admin` check in `mutation.py` turns a
  raw RLS policy violation into a clean, fail-closed error before the write is
  even attempted. Regression tests prove a write-scoped-but-non-admin
  principal cannot enqueue a job against a document outside its grants, and
  cannot call `createBackup`/`restoreBackup` without an admin role. See
  `engineering_decisions.yml` `D-019`.

## [1.9.0] - 2026-07-05

### Fixed
- **entity_frequency/entities corpus-wide query could peg every core for
  minutes** (Claude Sonnet 5 via Claude Code) — `entity_mention` and `chunk`
  now carry a `surface` column, populated at annotation write time instead of
  reconstructed via `substring(document_content.full_text, ...)` at read time.
  Without this, calling `entityFrequency`/`entities` without a `documentId` (a
  schema-permitted call shape) forced Postgres to extract and `GROUP BY` a
  substring of the full document text for every mention in the entire corpus
  before returning anything, since a `GROUP BY` blocks `LIMIT` pushdown — this
  pegged 8 parallel backends across every core for 7-17 minutes each against
  the live corpus. Migration 038 backfills existing rows and adds the column;
  `list_chunks`, `list_entity_links`, and `list_unresolved_mentions` were
  fixed the same way. Every pooled API connection also now carries a Postgres
  `statement_timeout` (`DOCRAY_API_STATEMENT_TIMEOUT_MS`, default 15s) as an
  independent guardrail against any future unbounded query.

## [1.8.0] - 2026-07-05

### Added
- **Evaluation and remaining NLP backlog completion** (OpenAI GPT-5 via Codex) —
  migration 036 adds evaluation/drift tables and migration 037 advances the
  unified package/schema version to `1.8.0`. `python -m
  doc_ray.entrypoints.evaluate` records corpus snapshots and base
  lexical/entity metrics with fact citations. `python -m
  doc_ray.entrypoints.annotate_semantic` populates deterministic discourse,
  TimeML date/event, sentiment, and hash-BoW embedding rows with provenance.
  PDF extraction can opt into an external Docling/Marker/Nougat-style command
  that must emit verified LaTeX-bearing JSON before output can be persisted.
  This closes `BL-002`, `BL-006`, and `BL-024`; the live backlog had no fourth
  remaining item.

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

[1.8.0]: https://semver.org/spec/v2.0.0.html
[1.7.0]: https://semver.org/spec/v2.0.0.html
[1.6.0]: https://semver.org/spec/v2.0.0.html
[1.5.0]: https://semver.org/spec/v2.0.0.html
[1.4.0]: https://semver.org/spec/v2.0.0.html
[1.3.0]: https://semver.org/spec/v2.0.0.html
