---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-02"
---

# Request: Seed domain vocabulary, then ingest and verify a book

## Inputs

- **Book**: `<path|url>` — if a URL, download it to `static/fixtures/` first; the pipeline ingests local files.
- **Domain vocabulary**: `<domain or explicit Wikidata QID list>` — the technical terms this book's canonicalization should be able to link.

## Task

Execute all five steps in order. Each step has a verification gate; do not
proceed past a failed gate — diagnose and fix first.

### 1. Preflight — stack

Bring up the **full** `docker-compose.yml` stack (API + worker + S3/object-store
env). The read-only `api.yml` stack accepts GraphQL queries but `ingestDocument`
uploads fail on it (no S3 credentials, no worker to drain jobs).

**Gate**: worker container is running and the API health endpoint responds.

### 2. Seed the canonical registry (QIDs, not terms)

Seeding has **no GraphQL mutation** — enqueue a `seed_canonical` job from inside
the worker container.

- Resolve each domain term to its Wikidata QID first (Wikipedia API
  `pageprops` for the matching article is the reliable route), and enqueue the
  job with a `qids` payload.
- Do **not** seed with a raw `terms` payload: Wikidata full-text top hits
  frequently resolve to the wrong entity (persons, albums, disambiguation
  pages) for technical vocabulary.

**Gate**: job completes with `seeded == <expected count>`; spot-check a few
registry rows for correct labels/QIDs.

### 3. Ingest the book

Use the GraphQL `ingestDocument` mutation (multipart file upload) against the
running API; the CLI (`python -m doc_ray.entrypoints.cli <path>`) is the
fallback if the upload path is unavailable. Poll the returned job to
completion and record the `documentId`.

**Gate**: job status `done`; document row exists.

### 4. Verify annotation and FTS

Ingested-but-unannotated documents have `language = NULL` and are indexed with
the `simple` tsvector configuration, while queries use `english` — search
looks "up" but returns degraded or empty results.

- Run a `searchSentences` query with a phrase known to be in the book; it must
  return hits.
- If the document's language is NULL or search misses, run the `reannotate`
  mutation for the document and re-verify.

**Gate**: `searchSentences` returns correct hits for at least two distinct
probe phrases.

### 5. Canonicalize against the seeded registry

Enqueue a `canonicalize` job for the document so the registry term scan
(Stage 1.5) and Stage-2 auto-link run. Leave `resolve` unset/false — the
LLM frontier resolver is an explicit, separately authorized cost.

**Gate**: job result reports `term_mentions > 0` and `linked > 0` for
vocabulary seeded in step 2; report the residue count.

## Acceptance criteria

- All five gates passed, with the evidence (job ids, counts, sample query
  results) included in the final report.
- Nothing is assumed to have worked because a job returned `done`: every
  pipeline output is verified by querying the resulting state.
- No commits; leave the working tree as found apart from the downloaded
  fixture.
