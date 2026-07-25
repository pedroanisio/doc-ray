---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Sonnet 5 via Claude Code (merge of Claude Opus 4.8 backlog + OpenAI Codex improvement-proposal candidates)"
  date: "2026-07-05"
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
corrected stale status drift for `BL-039` and `BL-043`.

**2026-07-05 evaluation and NLP completion:** completed the remaining parked
items `BL-002`, `BL-006`, and `BL-024` with a verified opt-in external PDF math
backend contract, deterministic semantic enrichment for layers 17–20, and
durable evaluation snapshots/metrics. No fourth backlog item existed in the
live backlog after these three.

**2026-07-05 codebase-improvement additions:** added `BL-047`-`BL-055` — from the FrameForge reference analysis (drift-gates, structured error envelope, property/golden tests, import-boundary test, graceful optional deps) plus session findings (phrase/semantic search, index-corruption monitoring).

**2026-07-05 document-surface UX/data-management audit:** added `BL-056`-`BL-063` from a document-surface capability audit (parallel webapp-UI and GraphQL/persistence/schema reviews against 9 claimed product gaps). Confirmed absent: thumbnails/previews, document collections, editable post-ingestion metadata, lifecycle states beyond ingestion status, bulk document actions, AI-suggested document metadata refinements, and an operator metadata-correction workflow. Also added a narrower item for the pagination/filtering gaps found once the already-shipped keyset pagination (`BL-009`/`D-008`) was excluded from scope.

**2026-07-05 enterprise-IAM gap analysis:** added `BL-064`-`BL-075` against a supplied checklist (Authentication: OIDC/SAML/local; Provisioning: SCIM; Session: cookie/JWT/refresh; Authorization: tenant RBAC/ABAC/resource-level; Audit: append-only/searchable/exportable), verified live against `security.py`, `mutation.py`, `018_rbac.sql`, and the `jobs`/`backups` grant migrations. Confirmed absent then: OIDC, SAML, SCIM, any tenant concept, webapp session/login, JWTs, refresh tokens, ABAC, and any security audit log (`document_provenance` is pipeline/domain history, not an access log). `BL-064` is a live confirmed bug, not a future-state gap: every mutation resolver checks only global read/write scope, never the caller's `document_acl` grant for the specific `document_id` — and `jobs`/`backups` carry no RLS at all — so any write-scoped token can currently mutate or delete any document in the corpus, not just ones it holds a grant on. `BL-065` has since landed as the foundational tenant primitive for the tenant-scoped items (`BL-067`, `BL-068`, `BL-073`, `BL-075`).

**2026-07-05 overlay-on-original-document feature design:** added `BL-076`-`BL-083`, decomposing the request "display the overlays/pages/rendering over the original document" into its component parts, verified against the live tree: PyMuPDF extraction already computes per-span bounding-box/page geometry (`extraction.py`'s `_spans_of_line`/`_blocks_from_pages`) but discards it before persistence (`BL-076`, the foundational blocker); `spans` stores only `char_start`/`char_end`, no page or coordinates (`BL-077`); the existing corpus would need re-extraction, not just re-annotation, to backfill that data (`BL-078`); no code path anywhere renders a PDF page as an image (repo-wide grep for `get_pixmap`/`pdf2image`/`Pixmap` returned zero hits) or streams the original file to a browser at all (`BL-079`, `BL-080`); the reader's "Pages" tab is a fixed 40-sentence chunk unrelated to real PDF page breaks, which the overlay view needs fixed first so both views agree on what "page 3" means (`BL-081`); and `BL-082` is the overlay view itself, with `BL-083` parked as a V2 (entity/canonical-link highlights reusing the same mechanism).

**2026-07-05 overlay feature, corrected for format coverage:** the design above was PDF-only; corrected after being told the corpus also ingests docx/html and should consider pptx/svg. Verified live: `.pptx` and `.svg` have zero extraction code or dependency anywhere in the tree today (format-support gaps, not overlay-scope questions — added `BL-084`, `BL-085`); `docx`/`html`/`epub` extraction already exists but captures no positional/DOM data at all (`extract_docx` is paragraph-text-only; the HTML/EPUB path discards the DOM), and none of the three have a PDF-style fixed page to rasterize, so they need a DOM/markup-highlighting approach instead of `BL-076`-`BL-082`'s render-page-then-draw-bbox model (added `BL-086`). `BL-084`/`BL-085` land ingestion only; `BL-087` (parked on both) extends the bbox-overlay pattern to PPTX (real per-slide shape coordinates via python-pptx) and SVG (native vector coordinates, no rasterization needed at all) once each format can be ingested. `BL-076`, `BL-079`, `BL-081`, and `BL-082` were retitled/re-scoped in place to state "(PDF)" explicitly rather than imply their approach generalizes.

**2026-07-05 full ingest feature design (pptx/svg + docx/html/epub structural parity):** added `BL-088`-`BL-093`, a from-scratch design (not overlay-scope) for production-ready multi-format ingestion, decomposed into hardening / new-format extraction / existing-format structural parity / test-coverage-drift-guard. Verified live: `extract_docx` (`extraction.py:511-518`) discards `python-docx`'s already-available `paragraph.style.name` ("Heading 1", "List Bullet", ...) entirely, and `_HTMLText` (`extraction.py:387-423`, reused by EPUB) flattens `h1`-`h6`/`li`/`p`/`div` identically to a bare newline — both are confirmed, currently-shipping structural-information losses, not hypothetical gaps (`BL-091`, `BL-092`). Separately, EPUB's zip/XML parsing (`extraction.py:445-491`) has no decompressed-size cap and no entity-expansion hardening today — a real, live gap against an already-accepted format, sequenced to land once, shared, before PPTX (also zip+XML) and SVG (raw XML) would otherwise copy the same unguarded pattern a third and fourth time (`BL-088`, `high`/`ready`, blocking `BL-089`/`BL-090`). `BL-089` (PPTX) and `BL-090` (SVG) are the new-format extractors themselves, each deriving Markdown heading/list structure from real format-native structure (placeholder type + paragraph level for PPTX; font-size/weight for SVG) rather than PDF's inference heuristic. `BL-093` is a coverage drift-guard (every `_EXTRACTORS` entry must have a golden-fixture test), mirroring the `BL-047`/`BL-052` precedent, rather than one-off per-format test tasks.

**2026-07-05 doc-management/webapp-UX lane, BL-063 landed:** implemented in worktree `doc-mgmt-lane`. `webapp/app.py`'s `documents()` and `search()` routes now page with `LIMIT`/`OFFSET` and a prev/next pager, matching the webapp's own established convention (`lexemes()`, `document()`'s sentence reader) rather than the GraphQL-only keyset-cursor machinery. The `documents` GraphQL query and `persistence_read.list_documents` gained `doc_type`/`status`/`tags` filter arguments (`tags` matches any-of, not all-of); `ReadRepository.list_documents`'s port signature was updated to match, keeping `test_read_surface_parity` green. New tests: two webapp pagination tests (`test_home_paginates_and_shows_next_link`, `test_search_paginates_and_shows_next_link`, plus their last-page counterparts) and one live-DB GraphQL filter test (`test_documents_filters_by_doc_type_status_and_tags`).

**2026-07-10 full-codebase gap analysis:** added `BL-094`-`BL-118` from six
parallel subsystem audits (object storage/content-addressing, worker/
job-queue robustness, webapp security surface, GraphQL input validation,
dependency/container supply chain, observability) run after `BL-064` landed
(commit `d04165d`). Each finding was independently verified against the live
tree with file:line evidence; the two most severe were re-verified a second
time directly. `BL-094` is the standout: `GET /backups/{id}/download`
(`entrypoints/api/app.py`) has **no authentication check at all** and runs on
the raw superuser pool connection rather than a principal-bound one, so it
also bypasses the `backups` RLS policy `BL-064`/migration 043 just added —
confirmed by the route's own test calling it with no auth header and
asserting 200. `BL-108` is the second critical finding: GraphQL introspection
and GraphiQL have no production gating, and anonymous read access is the
default, so an unauthenticated caller can enumerate the full schema
including admin-only mutation shapes. The rest span storage integrity/leaks
(`BL-095`-`BL-099`: orphaned blobs on delete, no read-side hash
verification, Content-Type silently downgraded on re-upload, no upload size
cap, no encryption enforcement), worker hang/crash-recovery gaps
(`BL-100`-`BL-102`), webapp CSRF/headers/rate-limiting/upload-validation
gaps (`BL-103`-`BL-107`), GraphQL argument-bound gaps distinct from
`BL-009`'s query-shape limits (`BL-109`, `BL-110`), container/supply-chain
hardening (`BL-111`-`BL-114`: root user, floating base image, no CVE/SBOM
scanning, CI bypassing the committed `uv.lock`), and an application-wide
absence of logging/tracing (`BL-115`-`BL-118`: worker and GraphQL failures
produce no durable trace today). Deduped against all 93 prior items before
writing; none duplicate `BL-009`/`023`/`038`/`039`-`046`/`049`/`055`/`058`/
`059`/`064`/`065`-`075`.

**2026-07-11 security-hardening pass:** completed `BL-108`, `BL-098`, and
`BL-088` (`BL-094` landed separately just before this pass). `BL-108`:
`build_schema` now takes a `debug` param that appends Strawberry's
`DisableIntrospection` extension unless `DOCRAY_API_DEBUG` opts in, and
`entrypoints/api/app.py` threads `settings.api_debug` into both `build_schema`
and the router's `graphql_ide` (`None` outside debug, so GraphiQL 404s instead
of rendering) — an anonymous caller can no longer enumerate the schema.
`BL-098`: `ingestDocument` now reads the upload in bounded chunks via a new
`Config.max_upload_bytes`/`DOCRAY_MAX_UPLOAD_MB` (the same knob the webapp's
Flask route already enforced), rejecting an oversized upload before the full
body is ever buffered. `BL-088`: `extraction.py`'s `ET` now aliases
`defusedxml.ElementTree` instead of stdlib (rejects DOCTYPE/entity payloads
outright), and a new `_check_zip_bomb_bounds` helper — configurable via
`DOCRAY_EXTRACT_MAX_UNCOMPRESSED_MB` — sums a zip's declared uncompressed size
before `extract_epub`/`extract_docx` read any member, both reusable as-is once
`BL-089`/`BL-090` add PPTX/SVG. All three verified end-to-end against the
live dev stack (real Postgres + MinIO), not just schema-level unit tests.

**2026-07-10 doc-management/webapp-UX lane, BL-058 landed:** implemented in worktree `doc-mgmt-lane`, schema bumped to `1.11.0` (migration 045 adds `documents.description` + the RLS-scoped `document_metadata_history` table; migration 046 bumps the version — renumbered from the branch's original 043/044 to resolve a numbering collision with BL-064's own 043/044, which landed on `main` first). Metadata edits go through the same job-queue write pattern as every mutation but `decide_seeds` (D-004/D-005) — a new `update_metadata` job kind, dispatched to `persistence.update_document_metadata`, which applies only the fields passed and records one history row per field that actually changed (a same-value edit is a no-op, no history row). `updateDocumentMetadata` enqueues it; `documentMetadataHistory` reads the trail back; the document page gained an inline edit form (pre-filled, collapsed by default) and a change-history card in the sidebar, with a "queued" notice on the redirect since the edit isn't applied synchronously. New tests across the stack: `persistence.update_document_metadata` behavior via the job handler (`test_handle_update_metadata_*`), a GraphQL enqueue-then-read-back test, and four webapp tests (form pre-fill, history rendering, successful redirect with the right mutation variables, and the error path).

**2026-07-11 backlog-vs-code audit:** all 118 items were verified against the
implementation (every `done` item's acceptance criteria checked in code; every
`parked`/`ready` item checked for silent implementation). Findings fixed the
same day: `BL-110` had two resolvers its sweep missed (`documentMetadataHistory`
leaked raw `invalid input syntax for type uuid`; `updateDocumentMetadata`
enqueued with no id validation and only global write scope) — both now
validate like their siblings, with regression tests. `BL-047`'s gate now also
runs in pre-commit (`gen_data_dictionary.py --check --skip-without-db`, an
explicit named skip when no DB is reachable; CI stays strict). `BL-048` landed
as `tests/test_docs_in_sync.py`, whose first run caught a real drift:
`DATABASE.md`'s §9 migration-history table had silently stopped at 037 because
`scripts/_data_dictionary_content.py`'s hand-curated `MIGRATIONS` list was
never extended — entries 038–046 added and the file regenerated. Stale
descriptions corrected on `BL-012`, `BL-051` (phantom MCP entrypoint),
`BL-069`, `BL-070` (its "no SECRET_KEY / no session" evidence predated
BL-103/BL-107), `BL-080`, `BL-084`, `BL-086`, `BL-091`, `BL-092`, `BL-111`,
and `BL-116`; rotted line-number citations replaced with symbol names.

**2026-07-11 supply-chain hardening lane (BL-111, BL-112, BL-114):**
implemented in worktree `supply-chain-lane` (branch
`feat/bl-111-112-114-supply-chain`), decision `D-024`. The Dockerfile is now
two digest-pinned stages: a deps stage that installs the runtime set from the
committed `uv.lock` (`uv sync --locked` via a digest-pinned uv binary — the
build fails on lock drift) into `/usr/local`, and a runtime stage that
switches to system user `docray` (uid 10001) before any entrypoint, with
`/app`/site-packages root-owned read-only and `/certs` pre-owned so
`nginx-certgen` bootstraps its volume unprivileged. Both CI jobs install the
same locked way and the static job gained a `uv lock --check` drift gate. New
guards: `test_app_base_image_is_pinned_to_an_immutable_digest` and
`test_dockerfile_drops_root_before_the_entrypoint` (extending BL-039's
pattern in `tests/test_compose_security.py`) plus `tests/test_uv_lock_sync.py`
(lock consistency + no-plain-pip in Dockerfile/CI, with self-tests proving the
checker can fail). Verified live on an isolated compose stack: every app
service healthy as uid 10001; a full ingest→annotate→semantic_enrich→
canonicalize pipeline and a scheduled backup ran end-to-end non-root on the
uv-built image.

**2026-07-12 pipeline-reprocess UX (operator request):** added `BL-119`
(per-document reprocess controls — re-extract/re-annotate/re-enrich/
re-canonicalize on the document page, wired to the mutations that all exist
today) and `BL-120` (bulk re-annotate/re-extract on BL-060's bulk-action bar,
bounded by `MAX_BATCH_IDS`). Gap surfaced while running the BL-078 geometry
backfill: the pipeline panel and BL-082's "re-extract needed" notice are
read-only dead ends — commanding the re-extraction required `docker exec` +
CLI. Both items are UI wiring over the existing thin GraphQL write path
(CSRF hook, Seed-style confirm, async "queued" notice), no new API surface.

**2026-07-12 pipeline observability API gaps (operator request):** added
`BL-121` (the GraphQL `Job` type hid the queue timing the `jobs` table
already stores — createdAt/startedAt/finishedAt/attempts were additive,
nullable field exposures) and `BL-122` (push-based pipeline progress —
subscriptions vs webhooks vs SSE laid out with their deployment and security
trade-offs; the operator later picked SSE). Both were confirmed live:
`information_schema` vs `types.py` for the former; no subscription type in the
Strawberry schema and a clean webhook/SSE grep for the latter.

**2026-07-12 async progress becomes first-class (operator decision):** the
operator selected **EventSource/SSE** as BL-122's push mechanism and named
async pipeline progress a first-class requirement — BL-122 later landed as
the SSE job stream, and its stream now also carries within-job progress; new
`BL-123` (ready/M/high) adds the progress source itself: `jobs` gains
`progress_current/progress_total/progress_stage` written by long-running
handlers at batch boundaries (extraction per page, annotation per sentence
batch, backup per table) on the existing heartbeat write cadence, exposed on
the GraphQL `Job`, rendered as a real progress bar in the pipeline panel,
and pushed over the SSE stream. Sequenced after BL-102 (job cancellation),
which is modifying the same queue plumbing as this is written.

**2026-07-12 test-isolation incident (operator question):** added and completed
`BL-124` (M/high). Local full-suite runs executed against the live/UAT database —
one evening of continuous fleet runs left 19 orphaned `itest` documents in
the corpus (BL-059's soft delete broke the old cleanup assumption; failed
runs skip teardown), `test_queue` claimed a real pipeline job off the shared
queue, and the restore tests replace live corpus state, coinciding with an
operator's uploads vanishing mid-demo. Fixed by a dedicated Dockerized test
database plus a loud local refusal to run DB-backed tests against non-test
databases unless `DOCRAY_TEST_ALLOW_LIVE_DB=1` is explicitly set; CI's
ephemeral service DB remains allowed.

**2026-07-12 overlay empty-page bug (operator report):** added and completed
`BL-125` (S/medium, bug). On a fully-reextracted PDF, `view=original` on a
text-free page (cover art, blank page) shows the document-level "re-extract
this PDF" message because the per-page dimension lookup finds no
`document_layout_runs` rows and conflates that with an unprocessed document —
verified on doc `060e4aeb` (pages 1/4 empty, pages 2–8 rendering fine). The
operator's hypothesis was exactly right; fixed by rendering the page image with
zero boxes and an honest "No text detected on this page" state.

**2026-07-12 empty-page root cause (operator request):** added `BL-126`
(ready/S/medium). BL-125 patched the consumer; the producer-side ambiguity
remains: extraction writes `document_layout_runs` only for pages containing
text, so "empty page", "unknown page", and "never extracted" all collapse
into one absent row — even though PyMuPDF knows every page's dimensions.
BL-126 persists per-page dimensions + an explicit has-text signal for every
page at extract/reextract, letting BL-125's fallback simplify and unblocking
future geometry consumers (BL-081 pagination, BL-087 overlays) from the same
trap. Existing corpus gains coverage via the standard reextract job.

**2026-07-12 lexeme metadata leak (operator request):** added and completed
`BL-127` (S/high). Anonymous GraphQL requests correctly saw no documents,
sentences, or entities under RLS, but `lexemes` was a global vocabulary surface:
it exposed corpus-derived lemmas/POS/language even when `lexeme_occurrences`
had been filtered away. Fixed by requiring at least one RLS-visible occurrence
for `lexemes`, `lexeme(uid:)`, and the webapp lexeme index.

**2026-07-12 auth_admin operator UX (operator bug report):** added and
completed `BL-128` (S/medium). Following an undocumented principal/token
snippet crashed the CLI with a raw psycopg traceback: `DATABASE_URL` was
unloaded and three modules carried contradictory host-side defaults
(`config.py` 5432 vs `webapp/app.py`/`_itdb.py` 5455). Fixed with a graceful
unreachable-DB path (exit 2, redacted DSN, points at `source .env` /
`--database-url`), one shared `DEFAULT_DATABASE_URL` in `doc_ray.config`
consumed everywhere (with a no-divergent-literal regression guard), and a
copy-paste-safe README quickstart for `principal create` / `token mint`.

**2026-07-12 gate restoration (operator directive):** mypy and ruff-format
gates restored on main the same session (`96d8599`); added `BL-129`
(ready/L/high — 12 files over the D-015 cap, per-file split list for parallel
consumption) and `BL-130` (ready/S/high — the signed-access-JWT acceptance
test fails on clean main, root-cause required). Both stem from fleet landings
running pytest but skipping the static gates.

**2026-07-12 collection-rollback bug (operator report):** added and completed
`BL-131` (XS/high). Collection create/member writes went directly through the
webapp's request connection — opened without autocommit — so request teardown
rolled back every write after the redirect had already been issued from inside
the doomed transaction. Fixed with autocommit on the request connection; the
regression test drives the real connection lifecycle across two requests,
because the suite's `client` fixture fakes `q`/`q1` wholesale and structurally
cannot see transaction bugs.

**2026-07-12 deploy-kill attempt burn (operator health-check triage):** added
and completed `BL-132` (S/high). Every fleet deploy SIGTERMs the worker;
long annotate jobs (45–90+ min) cannot finish inside the 120s stop grace, so
the SIGKILLed orphan was reaped as a stale heartbeat and the claim-time
attempt increment counted the deploy as a job failure — three deploys
permanently failed four books. Fixed with a drain stamp
(`jobs.drain_requested_at`, migrations 072/073): the heartbeat thread marks
the in-flight job on SIGTERM and the reaper releases stamped jobs back to
the queue with the attempt refunded, never failing them for exhaustion.
Crashes without the stamp still spend attempts (poison bound preserved), and
`claim()` now resets the dead attempt's progress fields so retries stop
rendering the old percentage.

**2026-07-12 span-geometry hot loop (operator directive):** added and
completed `BL-133` (S/high) — the root cause *behind* the day's reliability
symptoms. `span_geometry_from_layout_rows` scanned every layout row per span
(O(spans x rows); tens of billions of iterations for a 171k-span book) and
its caller's JOIN duplicated each layout row once per overlapping staged
span, skewing the per-page overlap vote. Now deduplicated, sorted once, and
bisected to a candidate window per span; the caller uses EXISTS. Regression
tests pin the page-vote semantics, equivalence with the original scan, and a
12k x 12k wall-clock budget (44.6s before, 0.1s after).

**2026-07-12 pipeline polish pass (operator directive):** added and completed
`BL-134` (S/high) — four regression-tested residual fixes: a janitor thread
reaps stale jobs every `WORKER_REAP_SECONDS` (default 30s) even while every
worker is busy (recovery previously waited 45+ minutes for a free slot);
`claim()` stamps attempt-relative `started_at` (retry ETAs no longer read
hours of elapsed time); reap clears progress on queued rows (no dead-attempt
percentages); and the layout fetch drops its correlated `_span_stage`
predicate (an O(rows x spans) planner nested loop in waiting).

**2026-07-12 backup heartbeat poisoning (found during ingest verification):**
added and completed `BL-135` (S/high). Progress reports wrote through the
worker's main connection; inside the backup handler's minutes-long snapshot
transaction each per-table report joined the transaction, held the jobs-row
lock until commit, and starved the heartbeat until the reaper stole the job
from a living worker — three attempts burned per backup once the corpus
outgrew the 120s reap window. Progress now flows through the heartbeat
connection under a shared mutex; the regression test proves the row stays
unlocked and progress stays visible during a REPEATABLE READ handler.

**2026-07-13 backlog-vs-codebase inspection (operator directive):** audited
all 135 statuses against the live tree. Spot-verified `done` claims held
(BL-064/094/108/131–135 all evidence-backed in code), but both `ready` items
had drifted: `BL-130`'s "red JWT test" had already been fixed in `ad9d62f`
the day it was filed — root cause was the fixture (a frozen `now=` minted a
token that expired at 12:15Z, a time bomb), not the authenticator — so it was
reconciled to `done`; `BL-129`'s violation inventory was stale (14 files over
the D-015 cap by re-measure, not 12; `webapp/app.py` grew 1234 → 1563 in the
design refresh) and was refreshed in place. The inspection also found a
second red gate no item tracked: `check_version_consistency.py` failing on
main (`__version__` 1.22.0 vs schema 1.24.0 — migrations 074–077 landed
without a version bump or CHANGELOG sections). Filed and fixed as `BL-136`
the same session: version bumped, 1.23.0/1.24.0 release sections
reconstructed from git history. `BL-129`'s split program was then executed
in full — all 14 over-cap files split into sibling modules (one commit each,
full suite green, every core module re-exporting the moved names so the D-019
grep checks and caller imports stayed stable), restoring
`check_file_size.py` to exit 0 on main. Four pre-existing red tests
(`test_api_refresh_tokens`, `test_read_surface_parity`, `test_doc_hygiene`,
`test_truncate_corpus`) were fixed first so the suite was green to split
against.

## Items

| ID | Item | Category | Type | Status | Cx | Priority | Owner |
|----|------|----------|------|--------|----|----------|-------|
| BL-001 | OCR pass for image-only PDFs | extraction | feature | done | M | high | shared |
| BL-002 | Math fidelity: recover equations as LaTeX via opt-in ML backend | extraction | feature | done | L | medium | shared |
| BL-003 | Calibrate heading-detection thresholds per corpus | extraction | tech-debt | done | S | low | mine |
| BL-004 | Render Markdown in the reading pane | webapp | feature | done | S | medium | mine |
| BL-005 | Full-document scroll view + solo-POS focus mode | webapp | feature | done | M | low | mine |
| BL-006 | Materialize the remaining NLP annotation layers | nlp | feature | done | XL | medium | concurrent |
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
| BL-024 | Evaluation and drift artifacts | nlp | research | done | L | medium | shared |
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
| BL-047 | Drift-gate generated docs with a --check twin | tooling | infra | done | XS | high | shared |
| BL-048 | Gate documentation prose against machine facts | testing | infra | done | S | high | shared |
| BL-049 | Structured result/error envelope for jobs and the API | api | feature | done | M | high | shared |
| BL-050 | Property-based invariants for ingest and annotation | testing | infra | done | S | medium | shared |
| BL-051 | Graceful optional-dependency handling with install hints | tooling | feature | done | S | medium | shared |
| BL-052 | Test-enforce the hexagonal import direction | testing | infra | done | XS | medium | shared |
| BL-053 | Golden-output harness with hash-lock and tolerance oracle | testing | infra | done | L | medium | shared |
| BL-054 | Expose phrase and semantic (vector) search on the GraphQL API | api | feature | done | M | medium | shared |
| BL-055 | Add index-health / corruption monitoring (amcheck + checksums) | ops | infra | done | M | medium | shared |
| BL-056 | Document thumbnails and visual previews | webapp | feature | done | L | medium | shared |
| BL-057 | Document collections: manual and rule-based smart collections | webapp | feature | done | L | medium | shared |
| BL-058 | Editable document metadata (rename, description, tags) with an audit log | api | feature | done | M | high | shared |
| BL-059 | Document lifecycle states beyond ingestion status (archive, soft-delete, restore) | persistence | feature | done | L | high | shared |
| BL-060 | Bulk actions for documents (tag, archive, move, delete) | api | feature | done | M | medium | shared |
| BL-061 | AI-suggested document metadata refinements (titles, tags, duplicates, grouping) | nlp | feature | done | M | medium | shared |
| BL-062 | Operator-facing document metadata correction and enrichment workflow | ops | feature | done | M | medium | shared |
| BL-063 | Paginate the main document list and add filter arguments to the documents query | api | tech-debt | done | S | medium | shared |
| BL-064 | Mutation resolvers never check per-document write ACL (any write token can mutate any document) | security | bug | done | M | high | shared |
| BL-065 | Introduce a tenant model as the foundation for tenant-aware RBAC | security | feature | done | XL | high | shared |
| BL-066 | OIDC authentication as the default authenticator | security | feature | done | L | high | shared |
| BL-067 | SAML SSO for enterprise tenants | security | feature | done | L | medium | shared |
| BL-068 | SCIM provisioning, authoritative for managed users | security | feature | done | L | medium | shared |
| BL-069 | Demote static bearer tokens to an explicit local/break-glass fallback | security | tech-debt | done | S | medium | shared |
| BL-070 | Webapp session: real per-user login with a secure HttpOnly cookie | security | feature | done | M | high | shared |
| BL-071 | Short-lived signed access JWTs for the GraphQL/REST API | security | feature | done | M | medium | shared |
| BL-072 | Refresh-token rotation with reuse detection | security | feature | done | M | medium | shared |
| BL-073 | ABAC layer for sensitive and contextual authorization rules | security | feature | done | L | medium | shared |
| BL-074 | Append-only security audit-event log distinct from document_provenance | security | feature | done | L | high | shared |
| BL-075 | Tenant-admin audit surface: search by actor/action/target/time/IP, exportable | security | feature | done | M | medium | shared |
| BL-076 | Persist per-span page number and bounding box at extraction time (PDF) | extraction | feature | done | M | high | shared |
| BL-077 | Schema: persist page number and bounding box on spans | persistence | feature | done | M | high | shared |
| BL-078 | Backfill page/bbox for already-ingested documents via re-extraction | ops | feature | done | L | medium | shared |
| BL-079 | Route: render an original PDF page as an image (PDF) | webapp | feature | done | M | medium | shared |
| BL-080 | Route: stream the original uploaded file to the browser | webapp | feature | done | S | low | shared |
| BL-081 | Align the reader's Pages tab to real PDF page boundaries (PDF) | webapp | tech-debt | done | S | medium | shared |
| BL-082 | Overlay view: POS/word highlights positioned over the rendered original page (PDF) | webapp | feature | done | L | medium | shared |
| BL-083 | Extend the overlay view to entity/canonical-link highlights (V2) | webapp | feature | done | M | low | shared |
| BL-084 | Extraction support for PPTX (PowerPoint) | extraction | feature | done | M | low | shared |
| BL-085 | Extraction support for SVG | extraction | feature | done | S | low | shared |
| BL-086 | Overlay strategy for non-paginated formats (docx/html/epub): highlight in a rendered view of the original markup | webapp | feature | done | L | low | shared |
| BL-087 | Overlay support for PPTX/SVG once ingested (slide/vector-native positioning) | webapp | feature | done | M | low | shared |
| BL-088 | Harden zip/XML parsing against decompression-bomb and entity-expansion attacks | security | bug | done | M | high | shared |
| BL-089 | Extraction support for PPTX: text and structure from slide/placeholder data | extraction | feature | done | M | medium | shared |
| BL-090 | Extraction support for SVG: text and structure from font-size/weight classification | extraction | feature | done | S | medium | shared |
| BL-091 | DOCX: reconstruct heading/list structure from paragraph styles | extraction | tech-debt | done | S | medium | shared |
| BL-092 | HTML/EPUB: reconstruct heading/list structure instead of flattening all block tags | extraction | tech-debt | done | M | medium | shared |
| BL-093 | Format-extraction coverage guard: every registered extractor has a golden-fixture test | testing | infra | done | XS | medium | shared |
| BL-094 | Backup download route is completely unauthenticated and bypasses RLS | security | bug | done | S | critical | shared |
| BL-095 | Duplicate re-upload silently downgrades a document's stored Content-Type | persistence | bug | done | XS | low | shared |
| BL-096 | No read-side integrity verification despite content_sha256 being threaded for exactly that purpose | persistence | tech-debt | done | S | medium | shared |
| BL-097 | deleteDocument never deletes the document's blob — permanent storage leak | persistence | bug | done | S | medium | shared |
| BL-098 | ingestDocument has no upload size cap — unbounded memory buffering | security | bug | done | S | high | shared |
| BL-099 | No encryption enforcement for object storage, at rest or in transit | security | tech-debt | done | M | medium | shared |
| BL-100 | Worker detects crashed jobs but not hung ones — no execution timeout | ops | bug | done | M | medium | shared |
| BL-101 | Worker's outer poll loop has no resilience to a dropped DB connection | ops | bug | done | S | medium | shared |
| BL-102 | JobStatus.CANCELED is a dead status — no code path ever sets it | ops | feature | done | S | low | shared |
| BL-103 | Webapp has no CSRF protection on any state-changing form | security | bug | done | M | high | shared |
| BL-104 | Webapp sends no security response headers (CSP, X-Frame-Options, etc.) | security | tech-debt | done | S | medium | shared |
| BL-105 | No rate limiting on expensive webapp routes (search, upload) | security | tech-debt | done | M | medium | shared |
| BL-106 | Upload extension is client-controlled and unchecked before the blob is stored | security | bug | done | S | medium | shared |
| BL-107 | Operator health token travels via URL query string, not a header | security | bug | done | XS | medium | shared |
| BL-108 | GraphQL introspection and GraphiQL are enabled with no production gating | security | bug | done | XS | high | shared |
| BL-109 | GraphQL write-mutation arguments lack the bounds every read resolver enforces | security | bug | done | S | medium | shared |
| BL-110 | Malformed UUID arguments leak raw Postgres exception text to GraphQL clients | api | bug | done | S | medium | shared |
| BL-111 | The app's own Docker base image floats on a mutable tag, unlike Postgres | ops | tech-debt | done | XS | medium | shared |
| BL-112 | App containers run as root — no USER directive anywhere in the Dockerfile | security | tech-debt | done | S | medium | shared |
| BL-113 | No automated dependency vulnerability scanning, SBOM, or update automation | tooling | infra | done | M | medium | shared |
| BL-114 | CI and the Docker build bypass the committed uv.lock; no hash verification | tooling | tech-debt | done | S | medium | shared |
| BL-115 | No structured logging anywhere in src/doc_ray — worker and GraphQL failures produce no durable trace | ops | tech-debt | done | M | high | shared |
| BL-116 | Webapp: 3 of 5 exception handlers never log to app.logger | webapp | bug | done | XS | low | shared |
| BL-117 | No request/correlation-id threading across webapp -> API -> queue -> worker | ops | feature | done | M | low | shared |
| BL-118 | Zero metrics/tracing integration anywhere in the stack | ops | feature | done | L | low | shared |
| BL-119 | Webapp: per-document pipeline reprocess controls (re-extract, re-annotate, re-enrich, re-canonicalize) | webapp | feature | done | S | medium | shared |
| BL-120 | Webapp: bulk reprocess actions (re-annotate / re-extract across a selection) | webapp | feature | done | S | low | shared |
| BL-121 | GraphQL Job type omits the queue timing the jobs table already stores | api | tech-debt | done | XS | medium | shared |
| BL-122 | Push-based pipeline progress: subscriptions, webhooks, or SSE instead of poll-only | api | feature | done | M | high | shared |
| BL-123 | First-class async job progress: within-job progress reporting from worker handlers, surfaced live | ops | feature | done | M | high | shared |
| BL-124 | Test suites run destructively against the live/UAT database — provision an isolated test database | testing | infra | done | M | high | shared |
| BL-125 | Original-overlay view mistakes a text-free PDF page for an un-reextracted document | webapp | bug | done | S | medium | shared |
| BL-126 | Persist per-page dimensions for every PDF page so a text-free page is a representable fact | persistence | tech-debt | done | S | medium | shared |
| BL-127 | GraphQL lexeme reads leak hidden-document vocabulary to anonymous callers | security | bug | done | S | high | shared |
| BL-128 | auth_admin CLI: raw traceback on unreachable DB, divergent host-side DB defaults, and no operator docs | tooling | bug | done | S | medium | shared |
| BL-129 | Restore the D-015 400-net-LOC gate: 14 files are over the cap after the recent landings | tooling | tech-debt | done | L | high | shared |
| BL-130 | test_api_jwt signed-access-JWT acceptance test fails on clean main | testing | bug | done | S | high | shared |
| BL-131 | Webapp collection writes silently rolled back — created collections vanished after the redirect | webapp | bug | done | XS | high | shared |
| BL-132 | Deploy-kills burned job attempts: long annotate jobs permanently failed by consecutive fleet deploys | ops | bug | done | S | high | shared |
| BL-133 | Span-geometry backfill was O(spans x layout rows): book annotation ran for hours and hit the execution timeout | nlp | bug | done | S | high | shared |
| BL-134 | Pipeline polish: janitor reap, attempt-relative started_at, reap progress hygiene | ops | tech-debt | done | S | high | shared |
| BL-135 | Backup heartbeat starved: progress writes rode the handler's snapshot transaction | ops | bug | done | S | high | shared |
| BL-136 | check_version_consistency gate red on main: package version left at 1.22.0 while schema moved to 1.24.0 | tooling | bug | done | XS | high | shared |
| BL-137 | docs-in-sync gate red on main: DATABASE.md stops at migration 094 while db/migrations holds 101 | docs | bug | ready | S | high | shared |
| BL-138 | No table is FORCE ROW LEVEL SECURITY, so any owner-role connection bypasses RLS entirely | security | bug | done | L | high | shared |
| BL-139 | Organization corpus: make unowned documents shareable within a tenant | security | feature | done | M | medium | shared |
| BL-140 | tests/test_webapp.py red on main: 16 tests fail on stale copy assertions and stale SQL stubs | testing | bug | done | M | high | shared |
| BL-141 | jobs RLS INSERT policy rejects non-admin member uploads (document-less ingest job); move enqueue to the trusted writer, jobs RLS read-only | security | bug | done | M | high | shared |
| BL-142 | Member corpus 500s: SECURITY DEFINER RLS helpers recurse on managed Postgres; documents.html formats a None n_sentences | security | bug | done | M | high | shared |
| BL-143 | Corpus page is a single dense table (~156px rows, ~5 docs/screen); add view modes (table/compact/gallery/grouped) + sort + density + group_by over the same rows | webapp | feature | done | M | medium | shared |
| BL-144 | MCP tests order-dependent: tests patch httpx.AsyncClient, so a lazy mcp streamable_http import inside a patched test dies on `X or None` annotation eval | testing | bug | done | S | high | shared |
| BL-145 | MCP layer is not self-teaching: no server instructions, no search coverage signal, opaque cursor rejections, thin tool descriptions | api | feature | done | M | high | shared |
| BL-146 | MCP reach and honesty gaps: no document map, raw NER type-splits, no token lever on the bulk readers | api | feature | done | L | high | shared |
| BL-147 | entity_profiles was dead for every real caller: form_index never granted to docray_client | api | bug | done | XS | high | shared |
| BL-148 | MCP evaluation harness: measure the tool surface instead of asserting it | testing | feature | done | M | medium | shared |

See [`backlog.yml`](backlog.yml) for each item's full description, rationale,
acceptance criteria, dependencies, related decisions (`D-###`), and references.

---

[← Back to root README](../README.md)
