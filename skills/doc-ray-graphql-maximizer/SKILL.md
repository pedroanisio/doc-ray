---
name: doc-ray-graphql-maximizer
version: 0.2.1
description: Enable AI agents to extract maximum value from the Doc-Ray GraphQL API. Use when an agent needs to explore, query, audit, extend, test, or operate Doc-Ray's Strawberry GraphQL surface; discover corpus contents; drill into documents, sentences, lexemes, entities, canonicalization, annotation layers, jobs, backups, uploads, or schema capabilities; or turn GraphQL access into evidence-grounded answers and next actions.
---

# Doc-Ray GraphQL Maximizer

Package version: see `VERSION`. The package README and CHANGELOG are present
because this skill is intended to be installed and updated across agent
environments, not only loaded from one checkout.

## Purpose

Use Doc-Ray GraphQL as an evidence engine, not just an endpoint. The goal is to
turn the corpus API into high-value answers: what exists, what it means, what is
missing, what needs curation, and what follow-up query or job unlocks more value.

## First Moves

1. Inspect the live schema before trusting docs.
   - Run `python skills/doc-ray-graphql-maximizer/scripts/schema_digest.py`.
   - Run `python skills/doc-ray-graphql-maximizer/scripts/check_doc_ray_drift.py`
     before updating the skill or relying on a long-lived installed copy.
   - If the task is API-extension or debugging work, also read
     `src/doc_ray/entrypoints/api/query.py`,
     `src/doc_ray/entrypoints/api/mutation.py`, and the relevant type file.
2. Establish the API visibility context before interpreting empty results.
   - GraphQL requests run through `docray_api` and Row-Level Security. Anonymous
     requests have no `app.principal_id`; an empty `documents` result may mean
     "not visible to this caller", not "no corpus".
   - For corpus discovery, first record whether the request is anonymous or uses
     bearer/`X-DocRay-API-Token` auth. Do not print token values.
   - If GraphQL is empty but another surface shows documents, read
     `src/doc_ray/entrypoints/api/security.py`, `webapp/app.py`, and
     `db/migrations/018_rbac.sql`, then use the visibility diagnostics in
     `references/graphql-value-playbook.md`.
3. Classify the user's goal:
   - **Corpus discovery**: documents, full-text search, sentence search.
   - **Document drill-down**: metadata, sentences, provenance, annotation runs,
     metadata history, tokens, dependencies, chunks, coreference, entity links.
   - **Lexical analysis**: lexemes, occurrences, KWIC evidence.
   - **Entity/canonical work**: entity frequency, seed suggestions, canonical
     search, unresolved mentions, resolution review.
   - **Pipeline/write operation**: upload, annotate, semantic enrichment,
     canonicalize, metadata update, seed approval, backup, restore, delete
     (soft), re-extraction/geometry backfill, job cancellation.
   - **Lifecycle & organization**: archive/restore documents, bulk archive/tag,
     metadata suggestions (suggest → review → decide).
   - **Operations & audit**: job progress/timing, SSE job events, stalled-job
     diagnosis, tenant-admin security audit events.
   - **API development**: schema, resolvers, read adapters, auth, pagination,
     tests.
4. Choose the smallest query set that preserves evidence. Prefer fields that
   return IDs, cursors, titles, surfaces, spans, statuses, and error/result
   payloads.
5. For "list all documents", use the bundled script instead of ad hoc curl or
   Python one-liners:
   - Anonymous: `python3 skills/doc-ray-graphql-maximizer/scripts/list_documents.py`
   - Authenticated from repo `.env`: `python3 skills/doc-ray-graphql-maximizer/scripts/list_documents.py --env-file .env --auth first-configured-token`
   - The script performs health/readiness checks, paginates all documents, and
     never prints token values.

## Operating Rules

- Treat `src/doc_ray/entrypoints/api/schema.py` and the root `Query`/`Mutation`
  classes as the current source of truth.
- Use GraphQL reads first. Use mutations only when the user explicitly wants a
  write, queue job, backup, restore, upload, or curation decision.
- Assume mutations require an authenticated principal with `write` scope. Use
  bearer auth or `X-DocRay-API-Token` only when the user/environment provides a
  valid token.
- Treat API visibility as caller-relative. State which principal/auth mode was
  used for every corpus inventory result.
- When a GraphQL corpus query returns no rows, do not conclude the database is
  empty until you have checked auth/RLS scope or an operator/database surface.
- Page every list using `limit` and `after`; preserve returned `cursor` values
  in findings so another agent can resume.
- For long-running operations, return the job ID and poll `job(id:)` or
  `jobs(documentId:)` until `status` is terminal. Request the progress fields
  (`progressCurrent`, `progressTotal`, `progressStage`) and timing
  (`createdAt`, `startedAt`, `finishedAt`, `attempts`) — a `running` job with
  a long-stale heartbeat is a stalled corpse, not active work. When the
  environment can hold a connection, prefer the SSE stream at `/jobs/events`
  over polling.
- Empty corpus listings have three causes, not one: RLS/auth scope, tenant
  scope, and document lifecycle (archived/soft-deleted documents drop out of
  default listings). Rule out all three before concluding "no data".
- For claims, cite stable evidence: document IDs, sentence IDs, ordinals,
  character spans, lexeme UIDs, mention IDs, canonical IDs, job IDs, and query
  text.
- For schema or behavior changes, validate with focused tests under
  `src/doc_ray/tests/test_api*.py` and related read-adapter tests.

## High-Value Query Patterns

Read `references/graphql-value-playbook.md` when writing actual operations or
planning a multi-step exploration. It includes query templates for:

- corpus inventory and search;
- document-level evidence extraction;
- annotation-layer inspection;
- lexeme/KWIC analysis;
- entity and canonicalization workflows;
- mutation/job flows;
- validation and API-extension work.

## Avoid These Traps

- Do not assume README prose is current; inspect the live schema.
- Do not fetch unbounded lists. Always use `limit`; continue with `after`.
- Do not collapse "entity mention" and "canonical entity"; mention detection and
  canonical linking are different layers.
- Do not treat a queued mutation as completed work. Completion is proven only by
  a terminal job and the expected downstream read result.
- Do not write directly to Postgres when GraphQL is the requested interface. A
  read-only database probe is acceptable only to diagnose an API/webapp/RLS
  visibility mismatch.
- Do not reveal configured API tokens while checking whether auth is available.

## Maintenance Scripts

- `scripts/install_or_update.py`: install or update this skill into
  `${CODEX_HOME:-~/.codex}/skills`, using `VERSION` for SemVer comparison.
- `scripts/check_doc_ray_drift.py`: verify the skill package and its bundled
  assumptions against the live Doc-Ray checkout: required API files, schema root
  fields, package README/CHANGELOG, and SemVer metadata.
- `scripts/list_documents.py`: list all GraphQL-visible documents with health
  checks, pagination, safe token loading, and table/JSON output.
- `scripts/schema_digest.py`: print a compact live schema digest for task-time
  planning.
