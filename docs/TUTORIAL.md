---
disclaimer: >-
  No information within this document should be taken for granted. Any
  statement or premise not backed by a real logical definition or verifiable
  reference may be invalid, erroneous, or a hallucination. Every command below
  was run against a live doc-ray stack to confirm its output; re-verify
  against your own checkout before relying on it, since schema version
  numbers and exact output will drift as the project evolves.
generated_by: "Claude Sonnet 5 via Claude Code"
date: "2026-07-11"
last_verified: "2026-07-11"
tool_versions:
  - tool: "Docker Compose"
    version: "v2 (docker compose, not docker-compose)"
  - tool: "doc-ray schema"
    version: "1.9.0 at verification time — expect this to have advanced"
---

# doc-ray Tutorial: From Clone to a Queryable Corpus, With AI Agents

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Overview

doc-ray is a living library and knowledge base (see [PURPOSE.md](../PURPOSE.md)).
Mechanically: you give it a file — PDF, EPUB, HTML, Markdown, plain text, DOCX, PPTX, or SVG — and it extracts the text and
metadata into a PostgreSQL corpus (full text, sentences, a 20-layer
linguistic annotation graph, entity canonicalization, semantic enrichment),
stores the original bytes content-addressed in MinIO/S3, and exposes the
result through a GraphQL API and a read-oriented Flask webapp.

This tutorial takes you from an empty checkout to a running stack with one
document ingested, annotated, and queryable — first through curl and the
webapp, then through the same GraphQL API the way an AI agent would use it.
It ends with a realistic account of what to expect: what runs synchronously,
what queues as a background job, and which of the 20 annotation layers are
real model output versus deterministic heuristics.

This is written for a reader who has never run doc-ray before but is
comfortable with Docker, the command line, and reading a GraphQL query. It
does not explain Postgres, Docker, or GraphQL themselves — see
[docs/architecture.md](architecture.md) for the system's internal design and
[docs/canonicalization_pipeline.md](canonicalization_pipeline.md) for how
entity linking works, once you're past this tutorial.

## Prerequisites

1. Docker with Compose v2: `docker compose version` prints a version line
   (not "command not found").
2. Git: `git --version` succeeds.
3. Python 3.12: `python3 --version` prints `Python 3.12.x` (only needed for
   the webapp and Part 2; the ingestion stack itself is fully containerized).
4. `curl`, for every API call in this guide.
5. About 3 GB of free disk (Postgres + MinIO volumes + Docker images,
   including the ~500 MB spaCy model image layer).

## Part 1: Install fresh and ingest a document

### Step 1: Clone the repository

```bash
git clone https://github.com/pedroanisio/doc-ray.git
cd doc-ray
```

You should now be in a directory containing `docker-compose.yml`,
`.env.example`, and `src/doc_ray/`.

### Step 2: Configure your environment

```bash
cp .env.example .env
```

Open `.env` and set `POSTGRES_PASSWORD` to a real secret. This is not
optional: Compose refuses to start Postgres with the historical default
(`docray`) or an empty/placeholder value.

```bash
# .env
POSTGRES_PASSWORD=a-real-secret-you-choose
```

Everything else in `.env.example` has a working local default.

**If you skip this step:** `docker compose up postgres` exits immediately
with an error naming `POSTGRES_PASSWORD`. Set it and retry.

### Step 3: Start Postgres and MinIO

```bash
docker compose up -d postgres minio createbucket
```

`createbucket` is a one-shot init container; it exits after creating the
MinIO bucket, which is expected and not a failure.

Verify both services are healthy:

```bash
docker compose ps --format "table {{.Name}}\t{{.Status}}"
```

Expected output (`docker-compose.yml` pins these two container names
explicitly, so they will match regardless of your Compose version):

```
NAME               STATUS
docray-postgres    Up ... (healthy)
docray-minio       Up ... (healthy)
```

**If `docray-postgres` shows `Restarting`:** run `docker compose logs
postgres`. The most common cause at this step is a `POSTGRES_PASSWORD` that
still matches the rejected default — re-check Step 2.

### Step 4: Start the API and worker

```bash
docker compose up -d api worker
```

(The `backup-scheduler` service is not started here: the in-app backup subsystem
is off by default and the scheduler exits immediately unless you set
`DOCRAY_BACKUP_ENABLED=true` — see [Backup & restore](../README.md#backup--restore).)

The schema migrates automatically on first start (`migrate` runs as an
init-once dependency of `api`). Verify with the API's own health and
readiness endpoints:

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/readyz
```

Expected output:

```json
{"status":"ok","version":"1.9.0"}
{"status":"ready","ready":true,"checks":{"database":"ok","object_store":"ok"},"version":"1.9.0"}
```

The exact `version` string will be whatever this repo's schema is at your
checkout — it is expected to differ from the value shown here.

**If `/readyz` returns `"ready":false`:** the JSON's `checks` object names
which dependency failed (`database` or `object_store`). Run `docker compose
logs api` and `docker compose logs postgres` (or `minio`) to see why.

### Step 5: Mint yourself a write-scoped API token

Anonymous GraphQL requests are deliberately read-only and RLS-scoped —
every mutation, including `ingestDocument`, rejects an anonymous caller with
`"API mutation requires an authenticated principal with write scope"`. Every
fresh install seeds one usable principal for exactly this purpose,
`bootstrap-admin` (`db/migrations/018_rbac.sql`):

```bash
docker compose exec -T postgres psql -U docray -d docray -t -A -c \
  "SELECT id FROM principals WHERE display_name = 'bootstrap-admin';"
```

Expected output: a single UUID, for example
`43d3d3d2-b0b3-4d1d-8e97-fb89efc2e8c3` (yours will differ — Postgres
generates a fresh UUID per install).

Add it to `.env` as an API token bound to that principal, then restart the
API so it picks up the new value:

```bash
# .env — append your own UUID from above
DOCRAY_API_TOKENS=dev-token=PASTE_THE_UUID_HERE

docker compose up -d api
```

**Do not commit this token, or paste it anywhere outside your own `.env`.**
It grants read+write access to the whole corpus.

### Step 6: Start the webapp

The webapp is not a Compose service — it runs on the host against the
same Postgres/API the containers expose on `localhost`.

```bash
pip install -r requirements.txt
python -m webapp.app
```

Expected output includes a line like:

```
 * Running on http://127.0.0.1:5001
```

Open <http://localhost:5001> in a browser. You should see the corpus landing
page reporting zero documents.

**If the page instead shows "Can't reach the database":** the webapp
connects to Postgres directly using `DATABASE_URL` from `.env`; confirm
`docker compose ps` still shows `docray-postgres` healthy.

### Step 7: Ingest a document

Use the bundled fixture so this step has a known, reproducible input, and
the token from Step 5 so the write is authorized:

```bash
TOKEN=dev-token   # the token string you set in .env, not the UUID
curl -s -H "Authorization: Bearer $TOKEN" \
     -F operations='{"query":"mutation($f:Upload!){ ingestDocument(file:$f, pipeline:true){ id status kind } }","variables":{"f":null}}' \
     -F map='{"0":["variables.f"]}' \
     -F 0=@static/fixtures/AirFrance447_BEA.pdf \
     http://localhost:8000/graphql
```

Expected output (a queued job, not a completed document — see
[What to expect](#what-to-expect) for why):

```json
{"data":{"ingestDocument":{"id":"01K...","status":"queued","kind":"ingest"}}}
```

Record the `id` value; it is the job id.

**If you get `"API mutation requires an authenticated principal with write
scope"`:** the `Authorization` header didn't reach the request — confirm
`TOKEN` matches the left-hand side of `DOCRAY_API_TOKENS` in `.env` exactly,
and that you restarted `api` after editing it.

**If you get an HTTP 400 about content type:** you are not using
`multipart/form-data` correctly — copy the `curl` command exactly, including
both `-F` flags before `-F 0=@...`.

### Step 8: Poll the job until it completes

```bash
curl -s -X POST -H 'content-type: application/json' -H "Authorization: Bearer $TOKEN" \
     -d '{"query":"query($id:ID!){ job(id:$id){ status kind error } }","variables":{"id":"PASTE_JOB_ID_HERE"}}' \
     http://localhost:8000/graphql
```

Repeat every few seconds until `status` reads `succeeded`. On a first run
this can take from several seconds to roughly a minute (spaCy loads its
model, then annotates, semantically enriches, and canonicalizes).

Expected final output:

```json
{"data":{"job":{"status":"succeeded","kind":"ingest","error":null}}}
```

**If `status` becomes `failed`:** the `error` field contains the exception
message. `docker compose logs worker` has the full traceback.

## Verification (Part 1)

Confirm the document is fully queryable, not just ingested:

```bash
curl -s -X POST -H 'content-type: application/json' -H "Authorization: Bearer $TOKEN" \
     -d '{"query":"{ documents(limit:5){ id title status charCount pageCount tokenCount } }"}' \
     http://localhost:8000/graphql
```

Expected output:

```json
{"data":{"documents":[{"id":"...","title":"F-GZCP VA","status":"extracted","charCount":477698,"pageCount":224,"tokenCount":89754}]}}
```

`documents` returns a flat list — there is no `items`/`pageInfo` wrapper;
each `Document` carries its own `cursor` field for keyset pagination
(`documents(limit:5, after:"<last cursor>")` for the next page).

`status` reads `extracted` once the base ingest step (text/metadata
extraction) has succeeded — it is the terminal success value for a document
row; there is no `ready` status. `extracted` does not by itself prove the
annotate/semantic_enrich/canonicalize chain finished — `tokenCount`,
`sentenceCount`, and `charCount` being nonzero is what confirms annotation
actually ran. If `status` reads `pending`, the base ingest job (Step 8) is
still running; if `failed`, check the job's `error` field.

Note the title came from the PDF's own metadata (`F-GZCP VA`), not the
filename — this is expected; doc-ray prefers embedded document metadata over
the uploaded filename when both are available.

You can also open <http://localhost:5001> and confirm the document now
appears on the landing page with its title and page count.

## Part 2: Using doc-ray with a real AI agent

Beyond curl and the webapp, this repository ships an agent-facing skill —
[`skills/doc-ray-graphql-maximizer/`](../skills/doc-ray-graphql-maximizer/) —
that teaches an AI coding agent (Claude Code, Codex, or similar) how to
explore and query the corpus correctly: inspecting the live schema instead
of trusting stale docs, paginating instead of fetching unbounded lists, and
distinguishing "no documents visible to this caller" from "empty corpus"
under doc-ray's row-level-security model. It reuses the token you already
minted in Part 1, Step 5 — no separate credential is needed for an agent.

### Step 1: Install the skill

For Claude Code, install it into your user-level skills directory:

```bash
python3 skills/doc-ray-graphql-maximizer/scripts/install_or_update.py \
  --dest ~/.claude/skills/doc-ray-graphql-maximizer
```

For Codex, omit `--dest`; it defaults to
`${CODEX_HOME:-~/.codex}/skills/doc-ray-graphql-maximizer`.

Expected output on a first install (there is no prior version to compare, so
the drift check passes and the install proceeds):

```
doc-ray-graphql-maximizer: OK
install: /path/to/doc-ray/skills/doc-ray-graphql-maximizer -> /home/you/.claude/skills/doc-ray-graphql-maximizer
installed doc-ray-graphql-maximizer 0.1.2 at /home/you/.claude/skills/doc-ray-graphql-maximizer
```

**If it refuses with "installed version is newer":** you already have a
newer copy installed than this checkout ships; pass `--force` only if you
intend to downgrade.

### Step 2: Point the agent's tooling at the running API

The skill bundles `list_documents.py`, which performs the health/readiness
checks and pagination an agent should always do before concluding anything
about corpus contents:

```bash
python3 skills/doc-ray-graphql-maximizer/scripts/list_documents.py \
  --env-file .env --auth first-configured-token --limit 5
```

Expected output (truncated to one row; after Part 1's ingest completed):

```
API: ok version 1.9.0
Ready: True (ready)
Auth: X-DocRay-API-Token:first-configured-token
| # | Title | ID | Status | Pages | Chars | Sentences | Tokens |
|---:|---|---|---|---:|---:|---:|---:|
| 1 | `F-GZCP VA` | `2da0cd66-...` | extracted | 224 | 477,698 | 2,310 | 89,754 |
```

Run it again with no `--auth` flag (or `--auth anonymous`) to see the
RLS-scoped anonymous view for comparison:

```
API: ok version 1.9.0
Ready: True (ready)
Auth: anonymous
No documents visible to this GraphQL caller.
```

Both outputs are correct simultaneously. This is the exact distinction the
skill exists to teach an agent: an empty anonymous result does not mean the
corpus is empty — it means the anonymous role has no `document_acl` grants,
which is the default until you create one.

### Step 3: Let the agent explore the live schema

Rather than trusting this tutorial's GraphQL field names as the API
evolves, an agent should introspect the schema directly:

```bash
python3 skills/doc-ray-graphql-maximizer/scripts/schema_digest.py
```

Expected output (abridged; the real output lists every field on every type):

```
# Doc-Ray GraphQL Schema Digest

Type count: 26

## Root queries
- `version`
- `documents`
- `document`
- `searchSentences`
...

## Root mutations
- `reannotate`
- `deleteDocument`
...
```

At this point, an agent following the installed skill's `SKILL.md` — read
[`skills/doc-ray-graphql-maximizer/SKILL.md`](../skills/doc-ray-graphql-maximizer/SKILL.md)
for its full operating rules — has everything it needs to classify a task
(corpus discovery, document drill-down, entity work, or a write/pipeline
operation) and query the running corpus with cited, verifiable evidence
instead of guessing at field names or silently assuming an empty result
means an empty database.

## What to expect

- **Mutations queue jobs; they do not return finished results.**
  `ingestDocument`, `reannotate`, `canonicalizeDocument`, and every other
  write in this API return a `Job` with `status: queued` immediately. The
  actual work happens in the `worker` process afterward. Always poll `job(id:
  ...)` (or `jobs(documentId: ...)`) until `status` is a terminal value
  (`succeeded` or `failed`) before treating an operation as done.
- **`documents.status` only ever reads `pending`, `extracted`, or `failed`.**
  There is no `ready` status. `extracted` means the base ingest step
  succeeded — it does not by itself prove the annotate/semantic_enrich/
  canonicalize chain finished. Check `tokenCount`, `sentenceCount`, or
  `entities` being populated (or poll the job chain itself) for that.
- **The NLP is real spaCy, not a mock.** Token, POS, dependency, chunk, NER,
  and coreference layers are genuine `en_core_web_sm` model output — the
  first annotation job after a fresh container start pays a one-time model
  load cost.
- **Not every annotation layer is model-grade, and this repo says so.**
  Per [docs/SHOWCASE.md](SHOWCASE.md)'s honesty notes: discourse relations,
  temporal expressions, sentiment, and embeddings are deterministic
  heuristics, provenance-stamped as such — they support drift/search
  experiments, not semantic-interpretation claims. Coreference is a
  string-match heuristic over proper nouns, also explicitly tagged.
  Subwords, constituency parsing, SRL, word-sense disambiguation, and AMR
  have schema and tests in place but no annotator wired in yet.
- **Anonymous API access is scoped, not superuser.** With the default
  `DOCRAY_API_ALLOW_ANONYMOUS=true`, an unauthenticated GraphQL request still
  runs under Postgres row-level security with no principal — it sees only
  rows an anonymous grant permits (typically none, until you grant one). Set
  `DOCRAY_API_ALLOW_ANONYMOUS=false` to make this fail closed with HTTP 401
  instead.
- **GraphQL introspection is off by default.** Since `DOCRAY_API_DEBUG` is
  unset by default, `schema_digest.py`'s local introspection (which imports
  the schema directly in-process) still works, but an interactive GraphiQL
  IDE or a raw `IntrospectionQuery` over HTTP against `/graphql` will not,
  by design.
- **First upload after a cold start is the slowest.** Expect the spaCy
  model load and the full `ingest -> annotate -> semantic_enrich ->
  canonicalize` chain to take up to roughly a minute on modest hardware for
  a document like the bundled `AirFrance447_BEA.pdf` fixture (224 pages).
  Subsequent uploads are faster once the worker process has the model
  loaded in memory.

## Troubleshooting

**`docker compose up` reports a port already in use.**
Another process (or a previous doc-ray checkout) is bound to `5432`, `9000`,
`9001`, or `8000`. Either stop it, or override the conflicting port in
`.env` (`POSTGRES_PORT`, `MINIO_PORT`, `MINIO_CONSOLE_PORT`, `API_PORT`).

**A GraphQL mutation returns `"API mutation requires an authenticated
principal with write scope"`.**
You called a write operation anonymously, or the token didn't reach the
request. Revisit [Part 1, Step 5](#step-5-mint-yourself-a-write-scoped-api-token),
and pass the token as `Authorization: Bearer <token>` or
`X-DocRay-API-Token: <token>`.

**A job stays `queued` forever and never becomes `running`.**
Confirm the worker container is actually up: `docker compose ps worker`. If
it is not, `docker compose up -d worker` and re-poll.

**`ingestDocument` succeeds but `tokenCount`/`sentenceCount` stay at zero.**
Check `job(id:)`'s `error` field for the specific stage that failed, then
`docker compose logs worker` for the traceback. A worker crash mid-job is
recoverable — restarting `worker` reclaims stale jobs after the configured
heartbeat timeout (`WORKER_CLAIM_TIMEOUT_SECONDS`, default 120s).

## Next Steps

- [docs/webapp_user_manual.md](webapp_user_manual.md) — the manual for both
  product surfaces: the web interface screen by screen, and the MCP tool surface
  AI agents work the corpus through, including the domain-seeding curation
  workflow and where the two surfaces meet.
- [docs/architecture.md](architecture.md) — the hexagonal/DDD internal
  design.
- [docs/canonicalization_pipeline.md](canonicalization_pipeline.md) — how
  entity mentions get resolved to canonical Wikidata identities.
- [docs/SHOWCASE.md](SHOWCASE.md) — the full ingestion tool-chain and
  per-layer provenance table.
- [skills/doc-ray-graphql-maximizer/references/graphql-value-playbook.md](../skills/doc-ray-graphql-maximizer/references/graphql-value-playbook.md)
  — query templates an agent uses for deeper corpus exploration once past
  this tutorial.
- The root [README.md](../README.md) — command reference for everything
  covered here, plus the CLI-only (non-API) ingestion path and quality-gate
  commands for contributing to doc-ray itself.
