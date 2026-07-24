---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-15"
---

# MCP Service — Implementation Status & Plan

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Exposes the doc-ray corpus to agentic AI over the **Model Context Protocol
(MCP)**, authenticating each caller to a doc-ray principal so Postgres RLS scopes
what they can read.

**Status: live in production and verified end-to-end.** The server ships **19
read tools** (12 corpus readers incl. `read_document_sentences`, `corpus_manifest`,
`corpus_shard`, `get_job`, `list_collections`, `get_collection` + 3
composition-grounding tools from `entrypoints/mcp/compose.py` + 4 Prism visual
tools… see §3) always registered, plus **17 opt-in write tools**
(reprocessing + curation + full collection lifecycle) registered only when
`DOCRAY_MCP_ENABLE_WRITE=true` — the default surface stays read-only. It also
declares MCP's **resources** primitive: 5 `docray://` URIs/templates (§3) that
address the same RLS-scoped reads by URI. It is
deployed via `make mcp-up`, routed at `https://doc-ray.com/mcp` behind the
Cloudflare tunnel, and confirmed with a real MCP client (initialize → tools/list
→ RLS-scoped tools/call). The **OAuth 2.1 Authorization Server** (originally
Phase 4) shipped in commit `93c0e99` (`application/oauth_server.py`,
`webapp/oauth_web.py`, migration `082_oauth_clients.sql`), so a connector like
Claude Desktop obtains a token via Dynamic Client Registration + PKCE with no
pre-shared secret. Sections marked **DONE** are as-built; unchecked items are
roadmap. Remaining: Phase-3 ops polish (`/health`/`/readyz`) and optional gRPC.

**Deploying it (as-built).** `make mcp-up` layers `docker-compose.mcp.yml` on the
managed stack; the `mcp` service front-ends `api:8000/graphql`, loopback-bound on
`:8080`, auth-enforced. Two production gotchas that were load-bearing:
1. **Cloudflare route ordering.** The tunnel matches published-application routes
   top-to-bottom, first match wins. The `doc-ray.com /mcp → http://mcp:8080` rule
   must sit **above** the `doc-ray.com * → webapp:5001` catch-all, or `/mcp` is
   shadowed and 302-redirects to the webapp login.
2. **DNS-rebind host allow-listing.** The MCP SDK rejects any `Host` other than
   localhost with **HTTP 421 "Invalid Host header"**. Set `DOCRAY_PUBLIC_BASE_URL`
   (or `DOCRAY_MCP_ALLOWED_HOSTS`) on the `mcp` service so `server.py` allow-lists
   the public host; without it the connector authenticates but every call 421s.
3. **Apply migrations first.** DCR 500s on a missing `oauth_clients` table if
   `082` was not applied — see `production-setup.md`'s migration-verification note.

---

## 1. Settled architecture

- **Transport: Streamable HTTP MCP.** MCP defines only two transports — `stdio`
  (local) and **Streamable HTTP** (remote). **There is no gRPC MCP transport**, so
  the requested "MCP via gRPC" cannot be a raw gRPC endpoint (no MCP client could
  reach it). gRPC, if needed, is a *separate* non-MCP gateway (Phase 4), not the
  MCP surface.
- **Auth = reuse, not reinvent.** doc-ray already resolves a bearer token / JWT to
  a `Principal` and binds Postgres RLS:
  [`HeaderTokenAuthenticator`](../src/doc_ray/entrypoints/api/security.py),
  `parse_api_tokens` ([config.py](../src/doc_ray/config.py)), `verify_access_jwt`,
  `apply_principal`. The MCP server does **not** duplicate this.
- **Stateless adapter, one security boundary.** Each tool forwards the caller's
  bearer token to `DOCRAY_API_URL /graphql` (the exact pattern in
  [webapp/graphql_client.py](../webapp/graphql_client.py)), inheriting principal
  resolution, RLS, tenant scoping, and the schema's depth/complexity limits. The
  MCP server holds no DB pool and grants nothing the GraphQL API wouldn't.

```
 Agentic AI (MCP client, Streamable HTTP)  ──Bearer──▶  MCP server (auth middleware
 validates → contextvar → FastMCP tools)  ──same Bearer──▶  /graphql → Principal → RLS → Postgres
```

**Tool results are owner-scoped.** Since migration 102 (D-026) a document is
readable by its owner, an explicit ACL grantee, or a tenant admin — no role
grants corpus-wide read. An agent therefore sees exactly the corpus of the
principal whose token it presents, which for a token minted against a regular
user account is that user's own documents, not the tenant's. Nothing in the MCP
layer enforces this; it inherits the boundary from the GraphQL API, as with
every other read rule.

## 2. Module layout

| Module | Depends on | Purpose |
|---|---|---|
| `src/doc_ray/entrypoints/mcp/tokens.py` | starlette + `mcp` | Per-call bearer-token resolution: live request wins, `caller_token` contextvar is the stdio fallback |
| `src/doc_ray/entrypoints/mcp/corpus.py` | httpx only | GraphQL-passthrough client + read-tool functions (SDK-independent) |
| `src/doc_ray/entrypoints/mcp/auth.py` | starlette only | Bearer/JWT validation + `BearerAuthMiddleware` (reuses `tokens`) |
| `src/doc_ray/entrypoints/mcp/mutations.py` | httpx only | Write tools (reprocessing + curation mutations); registered only when writes are enabled |
| `src/doc_ray/entrypoints/mcp/resources.py` | httpx only | `docray://` resources + RFC 6570 templates over the read tools (addressing layer) |
| `src/doc_ray/entrypoints/mcp/server.py` | `mcp` (new dep) | Thin FastMCP glue: register tools, mount middleware, expose ASGI `app` |
| `docker-compose.mcp.yml` | — | `mcp` service; dev forwards to `http://api:8000/graphql` (in-network), prod to `https://doc-ray.com/graphql` |

Splitting the substance (`corpus.py` + `auth.py`) away from the SDK glue
(`server.py`) means the tools and auth are testable and committable **without** the
new dependency; only `server.py` needs `mcp`.

## 3. Tool inventory

### Read tools (always registered)

The corpus readers below each map to one GraphQL read field
([query.py](../src/doc_ray/entrypoints/api/query.py)); a further **3
composition-grounding tools** (`briefing_pack`, `document_evidence`,
`entity_dossier`) shipped in [`entrypoints/mcp/compose.py`](../src/doc_ray/entrypoints/mcp/compose.py)
for cited-briefing workflows — see that module for their signatures.

| MCP tool | GraphQL field | Notes |
|---|---|---|
| `search_sentences(term, document_id?, limit)` | `searchSentences` | KWIC pre/hit/post; term-filtered. Returns `{hits, coverage, note}` — see §3.1 |
| `read_document_sentences(document_id, limit, after?, from_ordinal?, to_ordinal?, page?)` | `document { sentences }` | in-order read: **sequential** walk *or* **random-access** ordinal/page slice; verbatim text + citation offsets |
| `full_text_search(query, limit)` | `fullTextSearch` | ranked docs |
| `list_documents(limit, doc_type?, status?)` | `documents` | keyset `cursor` |
| `get_document(document_id)` | `document` | metadata + counts |
| `get_job(job_id)` | `job` | poll a job enqueued by a write tool |
| `entity_mention_corrections(document_id)` | `entityMentionCorrections` | the human NER-correction audit log for a document |
| `list_collections(limit, kind?, after?)` | `collections` | native containers; keyset `cursor` |
| `get_collection(collection_id, document_limit)` | `collection` | one collection + its member documents |
| `corpus_manifest(shard_count)` | `corpusManifest` | shard map for a map-reduce sweep: per-shard counts that sum to the total |
| `corpus_shard(shard_index, shard_count, limit, after?)` | `corpusShard` | one deterministic, disjoint slice of the corpus; keyset `cursor` |
| `entity_frequency(document_id?, entity_type?, limit)` | `entityFrequency` | top entities |
| `lexeme_kwic(uid, document_id?, limit)` | `lexeme { occurrences }` | KWIC is a **nested** resolver, not a root field |

**Reading a whole document, in order.** `search_sentences` is term-filtered — it
returns only sentences matching a keyword, so it can't cover a document
exhaustively. `read_document_sentences` is the sequential-read counterpart: it
returns every sentence in `ordinal` order, keyset-paged, so walking it to
exhaustion covers 100% of the text with no gaps and no keyword flooding. Text is
returned **verbatim** (never clipped — exhaustive delivery is the contract;
bound payload with `limit`), and each sentence carries `ordinal` +
absolute `charStart`/`charEnd` offsets so any excerpt stays citable.

```jsonc
// Sequential — walk a document start-to-finish: call with after=null, then feed
// the last row's cursor back as `after`, until a page comes back empty.
{"name": "read_document_sentences", "arguments": {"document_id": "9f14ccff-…", "limit": 200}}
// → [{"ordinal": 0, "text": "…", "charStart": 0, "charEnd": 47, "cursor": "eyJ…"}, …]
{"name": "read_document_sentences", "arguments": {"document_id": "9f14ccff-…", "limit": 200, "after": "eyJ…"}}
// → next 200 sentences … repeat until the result is []

// Random access — jump straight to a slice (no walking from the start):
{"name": "read_document_sentences", "arguments": {"document_id": "9f14ccff-…", "from_ordinal": 4000, "to_ordinal": 4200}}
// → sentences with ordinal 4000..4200 (inclusive)
// …or read one source page directly:
{"name": "read_document_sentences", "arguments": {"document_id": "9f14ccff-…", "page": 50}}
```

**Sweeping the whole corpus with N workers (map-reduce).** `corpus_manifest`
partitions the visible corpus into `shard_count` deterministic, disjoint parts
(`hashtext(id) mod shard_count`); `corpus_shard` returns one part. Fan out one
worker per shard, and because the per-shard counts sum to `totalDocuments` you
can *prove* full coverage rather than assume it.

```jsonc
// 1. plan the sweep (read once)
{"name": "corpus_manifest", "arguments": {"shard_count": 8}}
// → {"shardCount": 8, "totalDocuments": 412, "shards": [{"index": 0, "documentCount": 51}, …]}

// 2. each of the 8 workers pulls its shard, paging to exhaustion
{"name": "corpus_shard", "arguments": {"shard_index": 3, "shard_count": 8, "limit": 100}}
// → [{"id": "…", "title": "…", "cursor": "…"}, …]  → feed cursor back as `after` until []

// 3. for each document id, pull content with read_document_sentences / get_document.
//    Coverage check: Σ shard document counts == totalDocuments, no id in two shards.
```
A shard is the intersection of `hash mod N` and the caller's RLS scope, so
"the whole corpus" always means "the whole corpus this token may read."

### 3.1 Self-teaching surface — instructions, coverage, cursors (BL-145)

The observed failure mode of this server was never a missing capability. It was
an agent re-deriving the retrieval model mid-task: searching with common words,
reading the flooded result as *missing content*, and reaching for the ranged
reader only after the budget was spent. Three mechanisms move those lessons out
of the user's session and into the server, where they are paid once.

**1. Server instructions.** `server.SERVER_INSTRUCTIONS` is passed to `FastMCP`
and advertised in the `initialize` response, so a client sees it before the first
tool call. It states the model (retrieval, not reading; every result is citable),
the recommended path (`list_documents` → ranged `read_document_sentences` →
rare-term `search_sentences`), and the two traps below. Edit it in
[`server.py`](../src/doc_ray/entrypoints/mcp/server.py) — it is the cheapest
place in the system to fix an agent-behaviour problem.

**2. Search coverage.** `search_sentences` returns an object, not a bare list:

```jsonc
{"name": "search_sentences", "arguments": {"term": "Paul", "document_id": "9f14…", "limit": 200}}
// → {
//   "hits": [{"ordinal": 5, "pre": "…", "hit": "Paul", "post": "…"}, …],
//   "coverage": {"hit_count": 200, "ordinal_min": 5, "ordinal_max": 1904,
//                "limit": 200, "limit_reached": true},
//   "note": "Hit the limit — a common term can flood one region of the text …"
// }
```

`coverage.limit_reached` is the load-bearing field: when true the result is
**capped, not complete**, and `ordinal_min`/`ordinal_max` show which stretch of
the document the hits actually came from. A run that returns 200 hits all below
ordinal 400 in a 4,000-sentence book has not found "little about the ending" — it
has found too much about the beginning. `note` says so in words, and names the
fix: narrow the term, or read the range with
`read_document_sentences(from_ordinal=…, to_ordinal=…)`.

**3. Cursors are per-tool.** A `search_sentences` cursor is not valid for
`read_document_sentences`, and vice versa; they encode different keysets. Any
GraphQL error mentioning a cursor is now translated into a typed `bad_input`
carrying that fact plus the ordinal alternative, instead of an opaque rejection
that costs a call to interpret.

**Holding new tools to the same bar.** [`test_mcp_p0.py`](../src/doc_ray/tests/test_mcp_p0.py)
fails the build if any registered tool has a description under 80 characters, or
if a tool taking a `document_id` does not validate it locally (`_require_uuid`)
before spending a round-trip. Adding a tool means writing a description that
teaches its use — the gate is mechanical, not a review convention.

### Write tools (opt-in — `DOCRAY_MCP_ENABLE_WRITE=true`)

Registered **only** when writes are enabled, so the default surface stays
read-only (a disabled deployment does not even list these in `tools/list`). Each
forwards the caller's token into the API, whose guards remain authoritative:
`_require_write_audited` (authenticated principal **+ `write` scope** + billing
entitlement, audited) and `_require_document_write` (per-document ACL, BL-064). A
caller lacking scope/grant gets a typed `permission_denied`.

**Getting a write-scoped token.** A static `DOCRAY_API_TOKENS` entry can carry
`:read+write`. For OAuth connectors (Claude Desktop et al.), the Authorization
Server now issues `write` too — but only bound to the consenting user's RBAC
(`member`/admin get it, `reader` does not; see the OAuth field guide §5.4 and
D-032). So enabling `DOCRAY_MCP_ENABLE_WRITE` is necessary but not sufficient:
the connecting principal must also hold write-capable RBAC **and** own (or have a
grant on) the target documents. Every tool here
enqueues a **reversible** background job or a curation decision — no destructive
or admin verb (`delete`/`archive`/`backup`/`restore`/`cancel_job`) is exposed.
Job-returning tools return the `Job` shape (`id`, `status`, …); poll it with
`get_job`. See [`mutations.py`](../src/doc_ray/entrypoints/mcp/mutations.py) and
engineering decision **D-030**.

| MCP tool | GraphQL mutation | Returns |
|---|---|---|
| `reannotate(document_id, lang="en")` | `reannotate` | `Job` |
| `reextract_document(document_id)` | `reextractDocument` | `Job` |
| `annotate_document(document_id, lang="en")` | `annotateDocument` | `Job` |
| `semantic_enrich_document(document_id)` | `semanticEnrichDocument` | `Job` |
| `canonicalize_document(document_id, resolve=False)` | `canonicalizeDocument` | `Job` (LLM only if `resolve`) |
| `suggest_seeds(document_id, limit=40)` | `suggestSeeds` | `Job` |
| `suggest_document_metadata(document_id)` | `suggestDocumentMetadata` | `Job` |
| `update_document_metadata(document_id, title?, description?, tags?, authors?)` | `updateDocumentMetadata` | `Job` |
| `bulk_tag_documents(document_ids, tags)` | `bulkTagDocuments` | `[Job]` |
| `correct_entity_mention(document_id, action, char_start, char_end, entity_type?, surface?, new_char_start?, new_char_end?, original_type?, rationale?)` | `correctEntityMention` | `Job` (`apply_corrections`) |
| `decide_seeds(approve_ids?, reject_ids?)` | `decideSeeds` | `Int` (rows updated) |
| `decide_document_metadata_suggestions(approve_ids?, reject_ids?)` | `decideDocumentMetadataSuggestions` | `Int` |
| `seed_approved(document_id)` | `seedApproved` | `Job` |
| `create_collection(name, description="", kind="manual", rule?, doc_type?, status?, tags?)` | `createCollection` | `DocumentCollection` |
| `add_document_to_collection(collection_id, document_id)` | `addDocumentToCollection` | `DocumentCollection` |
| `remove_document_from_collection(collection_id, document_id)` | `removeDocumentFromCollection` | `DocumentCollection` |
| `update_collection(collection_id, name?, description?)` | `updateCollection` | `DocumentCollection` |
| `delete_collection(collection_id)` | `deleteCollection` | `Boolean` |

Collection tools return the `DocumentCollection` shape (`id`, `name`, `kind`,
`documentCount`, …), not a `Job` — the API applies them synchronously.
`create_collection`/`update_collection`/`delete_collection` need `write` scope;
`add`/`remove` additionally need a write grant on the document and a **manual**
(not smart-rule) collection. `update_collection` fixes a bad name (e.g. an
HTML-escaped one); `delete_collection` removes **only the container** — the
member documents are never touched (`document_collection_members` is
`ON DELETE CASCADE`, so only the membership rows go).

`update_document_metadata` now also edits **`authors`** (an ordered list — order
is kept; pass `[]` to clear), so an agent can correct wrong or empty author
credits. Like `tags`, it replaces the whole set.

`correct_entity_mention` is the agent-facing half of the NER verification layer.
The model's entity output is wrong some of the time, and `entity_mention` rows
are rebuilt by every annotation run — so a correction is recorded against the
*stable span identity* (`document_id` + character offsets) and re-applied on
every subsequent pass, rather than written into the derived table where the next
run would erase it. `original_type` and `rationale` are stored, so an agent's
override is attributable and reversible (PALS's Law). Read `entity_mention_corrections`
first to avoid re-recording a fix that already exists.

**Example — reclassify a mis-typed entity and confirm it applied:**

```jsonc
// What does the model currently say? (offsets come from search_sentences / read_document_sentences)
{"name": "entity_mention_corrections", "arguments": {"document_id": "f0b3d10c-…"}}
// → []  (nothing corrected yet)

// "London" was tagged PERSON; it is a place.
{"name": "correct_entity_mention", "arguments": {
   "document_id": "f0b3d10c-…", "action": "reclassify",
   "char_start": 16, "char_end": 22, "entity_type": "GPE",
   "original_type": "PERSON", "rationale": "London is a place, not a person"}}
// → {"id": "01J…", "kind": "apply_corrections", "status": "queued", "documentId": "f0b3d10c-…"}

// The fix is applied by the worker — poll it.
{"name": "get_job", "arguments": {"job_id": "01J…"}}
// → {"id": "01J…", "status": "succeeded", "result": {"corrections_applied": true}}
```

Use `reject` for a span that is not an entity, `add` for one the model missed
(pass `entity_type`), and `adjust_span` with `new_char_start` / `new_char_end`
when the entity is real but its boundaries are wrong.

**Example — enqueue re-annotation, then poll to completion:**

```jsonc
// tools/call → reannotate
{"name": "reannotate", "arguments": {"document_id": "f0b3d10c-…", "lang": "en"}}
// → {"id": "9d2…", "kind": "reannotate", "status": "queued", "documentId": "f0b3d10c-…"}

// tools/call → get_job (repeat until status is "succeeded"/"failed")
{"name": "get_job", "arguments": {"job_id": "9d2…"}}
// → {"id": "9d2…", "status": "running", "progressStage": "annotate", "progressCurrent": 3, "progressTotal": 12}
```

**Example — curate metadata suggestions:**

```jsonc
{"name": "suggest_document_metadata", "arguments": {"document_id": "f0b3d10c-…"}}   // enqueues verification
// … read the pending rows via GraphQL/webapp, then:
{"name": "decide_document_metadata_suggestions", "arguments": {"approve_ids": ["m1", "m2"], "reject_ids": ["m3"]}}
// → 3   (rows updated)
```

**Example — group documents in a real collection (not tags):**

```jsonc
// tools/call → create_collection (a named, browsable container)
{"name": "create_collection", "arguments": {"name": "Dune Series", "description": "Herbert fiction"}}
// → {"id": "c1a2…", "name": "Dune Series", "kind": "manual", "documentCount": 0}

// tools/call → add_document_to_collection (repeat per document)
{"name": "add_document_to_collection", "arguments": {"collection_id": "c1a2…", "document_id": "9f14ccff-…"}}
// → {"id": "c1a2…", "name": "Dune Series", "documentCount": 1}

// tools/call → get_collection (fetch it back with its members)
{"name": "get_collection", "arguments": {"collection_id": "c1a2…"}}
// → {"id": "c1a2…", "name": "Dune Series", "documentCount": 1, "documents": [{"id": "9f14ccff-…", "title": "Dune Messiah", …}]}
```

Prefer collections over ad-hoc tags when the intent is a named group: a
collection is a first-class container with its own view, whereas tags are just
labels. (A `smart` collection auto-populates from a `rule` + `doc_type`/`status`/
`tags` filters instead of manual membership.)

Not exposed via MCP: `ingestDocument` (multipart upload, not a plain mutation),
the sharing mutations (`shareDocument`/`revokeDocumentShare`), and every
destructive/admin verb. These remain available on the GraphQL API directly.

### Resources — `docray://` URIs and templates (always registered)

Alongside tools, the server declares MCP's **resources** primitive: addressable,
host-cacheable handles a client enumerates with `resources/templates/list` and
fetches with `resources/read`. They are **addressing, not capability** — each read
goes through the same read tools, so the caller's bearer token and Postgres RLS
scope the result identically, and a `resources/read` still lands its bytes in the
caller's context (the resource abstraction does not shrink payloads). Served by
[`resources.py`](../src/doc_ray/entrypoints/mcp/resources.py).

| URI / template | Type | Serves |
|---|---|---|
| `docray://doc/{document_id}` | `text/plain` | full text, one sentence per line; truncates **explicitly** at 200 000 chars and names the ranged template for the remainder |
| `docray://doc/{document_id}/sentences/{start}-{end}` | `text/plain` | sentences `start`..`end` (inclusive ordinals) — the bounded, citable slice |
| `docray://doc/{document_id}/pages/{page}` | `text/plain` | every sentence on one source page |
| `docray://collection/{collection_id}` | `application/json` | one collection + its member documents |
| `docray://corpus/shards/{shard_count}` | `application/json` | the corpus shard map (map-reduce plan) |

```jsonc
// resources/read — a bounded, in-order slice by URI instead of a tool call
{"uri": "docray://doc/9f14ccff-…/sentences/4000-4200"}
// → contents[0].text = the sentences, one per line

{"uri": "docray://doc/9f14ccff-…/pages/50"}     // one source page
{"uri": "docray://corpus/shards/8"}             // JSON shard plan
```

**Host-gated.** Declaring these makes them available on the protocol; a client
must still surface `resources/*` to the model before a `docray://` URI can be
read. Clients that expose only tools keep working unchanged — the tool surface is
unaffected.

## 4. Phased plan

### Phase 0 — Decisions & prerequisites  ·  **RESOLVED (as shipped in v1)**
- [x] **New `mcp` dependency approved** — `mcp==1.28.*` pinned in `requirements.txt`/`uv.lock`.
- [x] **Transport = Streamable HTTP MCP** (not gRPC); a separate gRPC gateway stays a Phase-4 option.
- [x] **v1 tool set = the 6 read tools above**, read-only scope.
- [x] **Placement** — runs alongside `api` (dev compose) or standalone against the public
      API (prod); both wired in `docker-compose.mcp.yml`.
- **Acceptance:** decisions realized in code (commit `7cd2c4a`).

### Phase 1 — SDK-independent core (no new dependency)  ·  size: M  ·  **DONE**
- [x] `corpus.py` — `gql()` async passthrough (forwards `tokens.current_token()`, which
      reads the live request so a mid-session token refresh keeps reaching GraphQL) + the
      6 tool functions.
- [x] `auth.py` — `bearer_from_request`, token/JWT validation reusing `cfg.api_tokens` +
      `verify_access_jwt`, `BearerAuthMiddleware`; the caller token now lives in
      `tokens.py` (`caller_token` contextvar as the stdio fallback).
- [x] **TDD** — `test_mcp_corpus.py` (token forwarded on auth'd calls, absent when
      anonymous, GraphQL error → `RuntimeError`, variable mapping) and
      `test_mcp_auth.py` (valid token passes + sets contextvar; unknown → 401; no
      token + `allow_anonymous=false` → 401; JWT path). Stub `httpx`/requests.
- **Acceptance:** ruff, mypy `--strict`, D-015 (≤400 net LOC/file), and the new
      tests green; no change to `requirements.txt`/`uv.lock`. ✅

### Phase 2 — MCP server + dependency  ·  size: M  ·  **DONE (bar manual smoke test)**
- [x] Add `mcp==1.28.*` to `requirements.txt`; `uv lock --refresh-package doc-ray`
      (D-024 passes with the CI-pinned uv 0.10.9); installed into `.venv`.
- [x] `server.py` — `FastMCP("doc-ray")`, register `corpus.TOOLS`, mount
      `BearerAuthMiddleware` on `streamable_http_app()`, expose `app`.
- [x] `docker-compose.mcp.yml` — `mcp` service on `:8080`; dev forwards in-network to
      `http://api:8000/graphql` (`depends_on: api healthy`), production runs standalone
      against `https://doc-ray.com/graphql` (no local `api` service, no `:8000`).
- [x] Validated the FastMCP glue against the **installed** SDK (`mcp==1.28.1`):
      `FastMCP.tool()`, `streamable_http_app() -> Starlette`, `add_middleware`.
- [x] **TDD** — `test_mcp_server.py` (6 tools registered; app is Starlette fronted by
      `BearerAuthMiddleware`; module exposes an ASGI `app`).
- [ ] **Smoke test** with a real MCP client (MCP Inspector / Claude) end-to-end:
      connect → auth with a minted token → `search_sentences` returns RLS-scoped rows.
      *(operator step — needs a running stack + live MCP client)*
- **Acceptance:** full quality bar green (ruff, mypy, D-015, `uv.lock` check, ≥90%
      coverage, version consistency); docs updated. ✅ — manual smoke test pending operator.

### Phase 3 — Ops & hardening  ·  size: S
- [ ] TLS front door (nginx location or Cloudflare Tunnel ingress) — never publish `:8001` raw.
- [ ] Per-agent token minting doc (`auth_admin token mint`, `read` scope) + rotation guidance.
- [ ] `/health`/`/readyz` for the MCP service; wire into the prod compose + deploy doc.
- [ ] Optional: request rate limiting mirroring the API's `rate_limit` posture.
- **Acceptance:** MCP service reachable only via the TLS front; ops runbook section added.

### Phase 4 — v2 (post-first-version, optional)  ·  size: L
- [x] **MCP OAuth 2.1 — DONE** (commit `93c0e99`). Shipped as a full OAuth 2.1
      **Authorization Server** (not just a resource server): RFC 7591 Dynamic Client
      Registration → `/authorize` (browser login + consent, PKCE S256) → `/token`
      (authorization_code + PKCE), reusing the existing RLS-bound access JWTs.
      See `application/oauth_server.py`, `webapp/oauth_web.py`, migration `082`.
- [ ] **Write tools** behind the `write` scope (ingest, tag, collections).
- [ ] **gRPC gateway** (if required) — a separate service exposing the same
      `corpus.py` tool functions to gRPC clients; reuses the passthrough unchanged.

## 5. Testing strategy
- **Unit (Phase 1, SDK-independent):** run directly with `.venv/bin/pytest` — no DB,
      no `mcp`. Stub `httpx` and the request object.
- **DB-backed / integration:** none required for v1 (the MCP layer never touches
      Postgres directly); if added later, use `scripts/test-in-docker.sh`.
- **End-to-end (Phase 2):** MCP Inspector or a Claude client against a running stack,
      asserting RLS scoping with two principals (one sees a doc, one doesn't).
- **TDD order** everywhere: failing test first, then implementation (repo mandate).

## 6. Quality gates (every commit)
`ruff check` + `ruff format --check` (D-011) · `mypy --strict` (D-012) ·
`check_file_size.py` ≤400 net LOC (D-015) · `check_version_consistency.py` ·
`uv lock --check` (D-024) · `pytest` ≥90% coverage (D-013).

## 7. Risks & mitigations
| Risk | Mitigation |
|---|---|
| `mcp` SDK is mid v1→v2 transition (v2 beta changes `FastMCP`→`MCPServer`) | Pin `mcp==1.*` (stable FastMCP + auth); isolate all SDK use in `server.py` so a future migration touches one file |
| SDK glue written from docs, not a live import | Phase 2 validates against the installed SDK before landing; core logic is SDK-free |
| Token double-validated (MCP middleware + GraphQL API) | Accepted — defence in depth; RLS stays authoritative in the API |
| Hostile/expensive queries | Forwarded queries inherit `API_MAX_DEPTH=12` / `API_MAX_TOKENS=2000` |
| Raw port exposure | Loopback bind + mandatory TLS front (Phase 3) |
| LLM tool output trusted downstream | Tool results are the corpus's, not the model's — PALS's Law is about *generation*; retrieval tools return verified DB rows |

## 8. Decisions (resolved for v1)
1. **Dependency** — `mcp==1.28.*` added (pinned to the stable v1 FastMCP line; v2.x is beta).
2. **Transport** — Streamable HTTP MCP; a separate gRPC gateway remains a Phase-4 option, not built.
3. **Scope** — read-only (6 corpus readers in v1; 3 composition-grounding tools added since — §3); write tools still deferred to Phase 4.
4. **Auth model** — forward the caller's bearer token / access JWT (v1); **OAuth 2.1 now shipped** as a full Authorization Server (DCR + PKCE), not merely a resource server (see the Status header and Phase 4).
5. **Placement** — dev co-locates with `api`; prod runs standalone against the public API. Both wired.

## 9. Sequencing
Phase 0 (decide) → Phase 1 (core) → Phase 2 (dep + server + smoke test) → Phase 3
(TLS/ops) → Phase 4 (OAuth/writes/gRPC). **Phases 0–2 are complete and the smoke
test passed live**; **Phase-4 OAuth 2.1 shipped early** (it was the connector
blocker). The next actionable work is Phase 3 ops polish (`/health`+`/readyz`,
per-agent token minting) and the remaining Phase-4 write tools.
