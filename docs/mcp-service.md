---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-26"
---

# MCP Service — Implementation Status & Plan

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Exposes the doc-ray corpus to agentic AI over the **Model Context Protocol
(MCP)**, authenticating each caller to a doc-ray principal so Postgres RLS scopes
what they can read.

**Status: live in production and verified end-to-end.** The server ships **35
read tools** (20 from `entrypoints/mcp/corpus.py` — incl. `read_document_sentences`,
`corpus_stats`, `corpus_manifest`, `corpus_shard`, `get_job`, `list_collections`,
`get_collection`,
`entity_mention_corrections`, and the 5 Prism visual tools — + 10 structure tools from
`entrypoints/mcp/structure.py` (`read_document_outline`, `entity_profiles`,
`term_frequency`, `document_classification`, `document_syntax`, the SLP Block D `entity_projection`, the
Block F hyper-context readers `lens_entities` / `entity_relations` / `related_entities`,
and the Block E `allusions` reader) + 3
composition-grounding tools from `entrypoints/mcp/compose.py`
+ 1 semantic-search tool + 1 mention-confidence review tool… see §3) always registered,
plus **33 opt-in write tools**
(reprocessing + curation + full collection lifecycle + projection review
`confirm_projection` / `reject_projection` + allusion curation `propose_allusion` /
`confirm_allusion` / `reject_allusion`) registered only when
`DOCRAY_MCP_ENABLE_WRITE=true`, and served at their **own endpoint**
(`/mcp/curate`) rather than widening `/mcp` — the read endpoint stays read-only
whatever the flag says (see §2.1). It also
declares MCP's **resources** primitive: 3 listable `docray://` entry points +
5 drill-down templates (§3) addressing the same RLS-scoped reads by URI, and its
**prompts** primitive: 4 user-selectable workflows (§3.7) that ship the corpus
methodology as parameterized plans. It is
deployed via `make mcp-up`, routed at `https://doc-ray.com/mcp` behind the
Cloudflare tunnel, and confirmed with a real MCP client (initialize → tools/list
→ RLS-scoped tools/call). The **OAuth 2.1 Authorization Server** (originally
Phase 4) shipped in commit `93c0e99` (`application/oauth_server.py`,
`webapp/oauth_web.py`, migration `082_oauth_clients.sql`), so a connector like
Claude Desktop obtains a token via Dynamic Client Registration + PKCE with no
pre-shared secret. Sections marked **DONE** are as-built; unchecked items are
roadmap. Phase 3 (health/readiness, rate limiting, token runbook) shipped in #18
(§3.8); the only remaining roadmap item is the optional gRPC gateway (Phase 4, gated).

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
- **Stateless Streamable HTTP.** Every tool/resource/prompt is a unary,
  externally authorized GraphQL-backed operation, so FastMCP runs with
  `stateless_http=True`. Initialization and later calls may land on different
  processes; the server issues no process-local session id that becomes a 404
  after restart. Per-request bearer validation remains the security boundary.
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

## 2.1 Two endpoints — read and curation (#36 Phase 2)

| Endpoint | Registry | Registered when |
|---|---|---|
| `https://doc-ray.com/mcp` | the read tools only | always |
| `https://doc-ray.com/mcp/curate` | read tools **+** the write tools | `DOCRAY_MCP_ENABLE_WRITE=true` |

**Why.** Every agent session downloads the advertised tool surface before its
first useful call. `DOCRAY_MCP_ENABLE_WRITE` used to *widen* `/mcp`, so on a
write-enabled deployment a read-only caller paid for 32 curation tools it would
never invoke. Measured: the read registry serializes to ~45.6K characters, the
write-enabled one to ~78.5K — the split gives a read-only caller back roughly 42%
of that cost.

**What the split does and does not guarantee.** The server guarantees `/mcp`
cannot be widened. It cannot guarantee the saving: MCP has no cross-server
routing, so each endpoint is a separate client-side entry, and a client that
configures **both** loads both tool lists and saves nothing. Point read-only
agents at `/mcp` and only curation agents at `/mcp/curate`.

**Not a weaker path.** Both endpoints are routes on one Starlette app behind one
middleware chain, so bearer auth, rate limiting, audit logging and the health
probes apply identically. `/mcp/curate` is not a lower-scrutiny door: every write
remains gated by the API's `write` scope and per-document ACLs, exactly as before.

**Migration (breaking).** A client that used `DOCRAY_MCP_ENABLE_WRITE` to get
write tools from `/mcp` will no longer see them there — repoint it at
`/mcp/curate`. The flag keeps its security meaning; it now decides whether the
curation endpoint exists at all rather than how wide `/mcp` is.

Deployment: nginx routes `location /mcp` as a **prefix**, so both endpoints reach
the same service; tightening it to an exact match would drop `/mcp/curate` to the
webapp catch-all (302 → `/login`, a silent break). The Cloudflare tunnel routes
by hostname dashboard-side and needs no change.

## 2. Module layout

| Module | Depends on | Purpose |
|---|---|---|
| `src/doc_ray/entrypoints/mcp/tokens.py` | starlette + `mcp` | Per-call bearer-token resolution: live request wins, `caller_token` contextvar is the stdio fallback |
| `src/doc_ray/entrypoints/mcp/corpus.py` | httpx only | GraphQL-passthrough read tools using the shared outbound client (SDK-independent) |
| `src/doc_ray/entrypoints/mcp/http_client.py` | httpx only | Process-wide, token-free GraphQL connection pool; explicit bounds and lifespan shutdown |
| `src/doc_ray/entrypoints/mcp/auth.py` | starlette only | Bearer/JWT validation + `BearerAuthMiddleware` (reuses `tokens`) |
| `src/doc_ray/entrypoints/mcp/structure.py` | httpx only | The 10 structure/SLP read tools: outline, `entity_profiles`, `term_frequency`, `document_classification`, `document_syntax`, and the lens-scoped `entity_projection` / `lens_entities` / `entity_relations` / `related_entities` / `allusions` (split from `corpus.py` for D-015) |
| `src/doc_ray/entrypoints/mcp/compose.py` | httpx only | The 3 composition-grounding read tools (`briefing_pack`, `entity_dossier`, `document_evidence`) |
| `src/doc_ray/entrypoints/mcp/semantic.py` | httpx only | The semantic-search read tool (`semantic_search_sentences`) |
| `src/doc_ray/entrypoints/mcp/mutations.py` | httpx only | The 31 write tools (reprocessing + curation mutations); registered only when writes are enabled |
| `src/doc_ray/entrypoints/mcp/mutations_surface.py` | httpx only | Dry-run-first exact-surface NER correction tool; resolves one explicit document or a provably complete collection below 200 members |
| `src/doc_ray/entrypoints/mcp/prompts.py` | `mcp` | The 4 user-selectable workflow prompts (§3.7) |
| `src/doc_ray/entrypoints/mcp/resources.py` | httpx only | `docray://` resources + RFC 6570 templates over the read tools (addressing layer) |
| `src/doc_ray/entrypoints/mcp/tool_meta.py` | `mcp` + tool registries | Authoritative human titles and all four behavioral hints for every live tool |
| `src/doc_ray/entrypoints/mcp/server.py` | `mcp` (new dep) | Thin FastMCP glue: register tools, enforce stateless Streamable HTTP, mount middleware, expose ASGI `app` |
| `docker-compose.mcp.yml` | — | `mcp` service; dev forwards to `http://api:8000/graphql` (in-network), prod to `https://doc-ray.com/graphql` |

Splitting the substance (`corpus.py` + `auth.py`) away from the SDK glue
(`server.py`) means the tools and auth are testable and committable **without** the
new dependency; only `server.py` needs `mcp`.

### Tool metadata contract

The enabled registry contains **65 tools**: 33 reads and 32 opt-in writes.
`entrypoints/mcp/tool_meta.py` owns a distinct, non-empty human `title` and
explicit `readOnlyHint`, `destructiveHint`, `idempotentHint`, and
`openWorldHint` values for every one. `server.py` passes the title separately to
`FastMCP.tool(title=...)`; `annotations.title` stays unset, so the model-facing
label is not confused with the annotations object or the callable's description.
Bidirectional tests fail for either an unclassified live tool or a stale policy
name.

Read tools are read-only, non-destructive, idempotent, and closed-world. Write
tools are classified from their actual behavior: calls that can remove or
replace curated state are destructive, and calls that enqueue fresh work or
create a new durable row are non-idempotent. Four queued operations are
open-world because their worker can cross the corpus boundary:
`ingest_document` (arXiv), `canonicalize_document(resolve=True)` (configured
frontier resolver), `suggest_seeds` (Wikipedia/Wikidata), and `seed_approved`
(Wikidata). Every other tool is closed-world.

`correct_entity_surface` is the deliberate mixed-contract exception: its
default path is a read-only preview, while an explicit commit appends audit/job
rows but converges the derived mention set on the same exact rule. Its advertised
hints are therefore `readOnlyHint=false`, `destructiveHint=false`,
`idempotentHint=true`, and `openWorldHint=false`, as required by #41. These are
still model-facing planning hints, not permission or transactional guarantees.

These hints are **advisory**, not enforcement. GraphQL write scope, billing
entitlement, per-document ACL checks, and Postgres RLS remain authoritative. A
client must still confirm destructive actions rather than treating the hint as
permission.

At the #35 checkpoint, compact `tools/list` JSON for 64 tools grew from
**63,438 bytes** without metadata to **71,969 bytes** with all titles and hints:
**8,531 bytes** of one-time discovery overhead. After #41 adds
`correct_entity_surface`, and #46 teaches verified phrase counts. The measured
write-enabled surface is now **65 tools / 78,384 bytes** (~19,596 estimated
tokens); read-only is **33 / 45,466** (~11,366). Instructions are 2,915
characters in both modes. The evaluator baseline owns these current discovery
totals.

## 3. Tool inventory

### Read tools (always registered)

The corpus readers below each map to one GraphQL read field
([query.py](../src/doc_ray/entrypoints/api/query.py)); a further **3
composition-grounding tools** (`briefing_pack`, `document_evidence`,
`entity_dossier`) shipped in [`entrypoints/mcp/compose.py`](../src/doc_ray/entrypoints/mcp/compose.py)
for cited-briefing workflows — see that module for their signatures.

`briefing_pack` ranks each matched document's passages by **learned semantic
similarity** (the same sentence encoder behind `semantic_search_sentences`),
RLS-scoped, so a paraphrase surfaces even with no shared keywords (#33). The
ranking lives once in the shared `application.briefing` service, so MCP
`briefing_pack` and GraphQL `briefingPack` stay byte-equivalent for the same visible
slice. When the sentence stack is not installed, or a document has no visible
sentence vectors, it falls back **deterministically** to full-text/keyword ranking
and says which path ran in `retrieval` (and in `gaps` when it fell back) — never
silently. Document discovery remains full-text.

Every `limit` argument is served up to the platform page-size maximum of
**200** (`shaping.MAX_PAGE_SIZE`, kept equal to the GraphQL context's
`MAX_PAGE_SIZE` by a contract test). The clamp is never silent at this surface
(BL-162): each tool forwards the limit it will actually be served, and a
`search_sentences` request over the cap reports itself via
`coverage.requested_limit` and the `note`. Keyset paging always passes the
**last** row's `cursor` as `after` — a non-last row's cursor re-serves the rows
after it, overlapping the page you already hold.

These composition tools never present a capped page as if it were the whole
corpus. `entity_dossier` draws its **document roster from `full_text_search`** (the
complete set of documents containing the entity, independent of `max_mentions`)
and returns `cited_mentions` (the passages shown — a page, not a total),
`document_count`, and a `truncated` flag; when the cited page or the roster hits
its cap it says so in `gaps` and tells the agent, in `guidance`, not to claim
totals. A document in the roster with `cited_mention_count: 0` contains the entity
but had no passage cited on this page. Example:

```jsonc
{"name": "entity_dossier", "arguments": {"name": "Scytale", "max_mentions": 3}}
// → { "entity": "Scytale", "cited_mentions": 3, "document_count": 2, "truncated": true,
//     "documents": [ {"document_id": "…chapterhouse…", "cited_mention_count": 3, …},
//                    {"document_id": "…dune-messiah…", "cited_mention_count": 0, …} ],
//     "gaps": ["Cited passages were capped at max_mentions=3; more mentions … exist. …"],
//     "guidance": "State nothing … This is a capped sample, not the complete record …" }
```

| MCP tool | GraphQL field | Notes |
|---|---|---|
| `search_sentences(term, document_id?, limit, after?, match?, response_format?)` | `searchSentences` + `searchSentencePage` | `match="substring"` is unchanged containment; `match="phrase"` verifies `surface_word_v1` and adds occurrences/profile/candidate coverage. Returns `{hits, coverage, note}` — see §3.1 |
| `read_document_sentences(document_id, limit, after?, from_ordinal?, to_ordinal?, page?, response_format?)` | `document { sentences }` | in-order read: **sequential** walk *or* **random-access** ordinal/page slice; verbatim text + citation offsets |
| `read_document_outline(document_id, max_entries)` | `documentOutline` | the map that aims the ranged reader: addressable ordinal ranges + `source` provenance — see §3.2 |
| `full_text_search(query, limit, mode)` | `fullTextSearch` | ranked docs; `mode="terms"` requires all stemmed terms in any positions, while `mode="phrase"` requires them in order |
| `list_documents(limit, doc_type?, status?, after?)` | `documents` | keyset `cursor` |
| `get_document(document_id)` | `document` | metadata + counts |
| `get_job(job_id)` | `job` | poll a job enqueued by a write tool |
| `entity_mention_corrections(document_id)` | `entityMentionCorrections` | the human NER-correction audit log for a document |
| `uncertain_mentions(document_id, limit, entity_type?, after?)` | `uncertainMentions` | uncorrected mentions ranked least-trustworthy first; heuristic triage with reasons, not a calibrated probability |
| `document_classification(document_id)` | `documentClassification` | the document's genre axis; `status` tells you if it is a `pending` guess or a `confirmed` human decision |
| `document_syntax(document_id, limit?, after?)` | `Document.constituents` + `Document.srlFrames` | NLP Layers 9 and 14: the phrase tree (`S`/`NP`/`VP`/`PP`/`ADJP`/`ADVP`/`SBAR`, `parentId` null at each sentence root) and predicate-argument frames (`ARG0`…`ARG2`, `ARGM-LOC`/`TMP`/`MNR`/`NEG`/`ADV`). Both are DERIVED from the dependency parse, not a constituency parser or neural SRL: `frame` is the predicate lemma (`found`), never a PropBank roleset (`found.01`), and a non-projective phrase is recorded by its covering span. Every node and argument carries `charStart`/`charEnd`, so each is citable like a sentence hit |
| `entity_projection(document_id, lens_id?, limit, after?)` | `entityProjection` | `project(mention, lens)` — each mention's identity within a lens; `scope` local/global, `canonicalId` null = unresolved, `status` proposed/confirmed/rejected (Block D) |
| `lens_entities(lens_id, limit, after?, min_mentions?)` | `lensEntities` | a lens's significant entities, most-mentioned first (Block F) — type profile + consistency + `refreshedAt`; keyset `cursor` |
| `entity_relations(lens_id, canonical_id, limit, min_strength?, after?)` | `entityRelations` | an entity's direct co-occurrence relations (Block F); `strength` is NPMI — a structural measure, NOT a real-relationship claim |
| `related_entities(lens_id, canonical_id, hops, limit)` | `relatedEntities` | bounded N-hop discovery (Block F); `hops` capped at 3, over-cap rejected |
| `allusions(canonical_id, lens_id, direction, limit, after?)` | `allusions` | typed cross-lens edges for an entity (Block E) — `direction` outgoing/incoming/both; an edge is NOT identity and NOT transitive |
| `list_collections(limit, kind?, after?)` | `collections` | native containers; keyset `cursor` |
| `get_collection(collection_id, document_limit)` | `collection` | one collection + its member documents |
| `list_lenses(limit, after?)` | `lenses` | the collections marked as entity-resolution scopes (Block B); keyset `cursor` — see §3.5 |
| `corpus_manifest(shard_count)` | `corpusManifest` | shard map for a map-reduce sweep: per-shard counts that sum to the total |
| `corpus_shard(shard_index, shard_count, limit, after?)` | `corpusShard` | one deterministic, disjoint slice of the corpus; keyset `cursor` |
| `entity_frequency(document_id?, entity_type?, limit)` | `entityFrequency` | **raw** NER rows, one per (surface, type) — prefer `entity_profiles` |
| `entity_profiles(document_id?, entity_type?, limit)` | `entityProfiles` | one row per entity: merged spellings, measured type spread, `typeConsistency` — see §3.3 |
| `term_frequency(terms, document_id?)` | `termFrequency` | flood-risk pre-flight: exact single-lexeme counts (`method: lexeme_lemma`), or bounded candidates plus `surface_word_v1` verified sentence/occurrence counts for phrases/compounds (`method: sentence_scan`); corpus-wide only (`document_id` → typed `unsupported`) — see §3.6 |
| `lexeme_kwic(uid, document_id?, limit)` | `lexeme { occurrences }` | KWIC is a **nested** resolver, not a root field |
| `semantic_search_sentences(query, document_id?, limit)` | `semanticSearchSentences` | meaning-based (learned-embedding) sentence retrieval; the antidote to lexical `search_sentences` flooding. Typed `unsupported` where the sentence stack is absent |
| `document_images(document_id, limit, after?)` | `documentImages` | Prism visual layer: extracted/rendered page images |
| `image_class_summary(document_id)` | `imageClassSummary` | per-document tally of image tier classes |
| `visual_captions(document_id, limit)` | `visualCaptions` | generated captions for a document's images |
| `cross_modal_image_search(query, limit)` | `crossModalImageSearch` | text→image search; typed `unsupported` where the vision stack is absent |
| `visual_review(document_id?, status, limit, after?)` | `visualReview` | RLS-scoped Tier-3 caption proposals and their human-review status |

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
//                "limit": 200, "limit_reached": true, "exhaustive": false},
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

**`coverage.exhaustive` — the affirmative half (BL-164).** The above announces
*incompleteness*; success used to be silent, which is the harder problem for a
caller writing grounded prose. `hit_count: 3` could not be told apart from "the
first page of many", and `hits: []` could not be told apart from a bad query — so
consumers hedged their absence claims, or ran sharded corpus sweeps to re-derive
what a single call already knew. `exhaustive: true` states it: the page did not
fill **and** no cursor preceded it, so `hit_count` **is** the total for that term
in the scope searched.

A zero-hit first page also says it in prose, bounded on both axes — to the scope
actually scanned (`this document` vs `the corpus visible to you`, since RLS scopes
every read to the caller's tenant) and to the *literal string*, because absence of
a spelling is not absence of a subject:

```jsonc
// → {"hits": [], "coverage": {"hit_count": 0, "limit_reached": false, "exhaustive": true},
//    "note": "0 hits. This was an exhaustive case-insensitive substring scan of the corpus
//             visible to you, not a sample — 'pgbouncer' does not occur in it as a literal
//             string. Absence of the exact string is not absence of the idea: try a different
//             spelling, hyphenation, or spacing, or search a synonym …"}
```

Paging past the end is deliberately **not** exhaustive: zero hits after an `after`
cursor means "no more after this cursor", never "absent from the corpus" — the
earlier pages had hits and were not re-scanned. Treating that as absence would
just be a new false negative, so the note says page-boundary instead.

Phrase mode adds candidate-scan evidence because post-filtering can exhaust its
independent 2,001-row work budget before the requested hit page fills:

```jsonc
{"name":"search_sentences","arguments":{"term":"Algorithm Design","match":"phrase","limit":20}}
// → {"matchProfile":"surface_word_v1",
//    "hits":[{"ordinal":42,"hit":"Algorithm  Design","occurrenceCount":2,
//             "occurrences":[{"charStart":140,"charEnd":157},{"charStart":201,"charEnd":217}]}],
//    "coverage":{"hit_count":1,"limit_reached":false,"exhaustive":true,
//                "candidate_scanned":9,"candidate_limit":2001,
//                "candidate_limit_reached":false},"note":null}
```

If `candidate_limit_reached` is true, `limit_reached` and `exhaustive` also say
the answer is truncated even when `hits` is empty. The note names phrase mode and
the profile; a verified zero below the cap likewise says it was a
`surface_word_v1` whole-word phrase verification, not a substring or stem scan.

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

### 3.1.1 What `search_sentences` matches: substring or verified phrase

A reviewer searched `Hick` corpus-wide, got back `thick`, `thicker`, `thickness`,
and concluded the tool had lemma-normalized a proper noun — then, because the
zero-hit note on a *scoped* search mentioned a substring scan, concluded that
`search_sentences` had **two implicit matching semantics under one name**:
lemma-based corpus-wide, substring when scoped. Both readings were wrong. Its
default has one predicate
([`persistence_sentence_search.search_sentences`](../src/doc_ray/adapters/persistence_sentence_search.py)),
one `ILIKE`, and `document_id` only adds a `WHERE`. `thick` matched because it
*contains* `hick`. Issue #45 adds a second semantic only as the explicit,
opt-in `match="phrase"`; omitting `match` is byte-compatible substring mode.

The inference was wrong; the complaint underneath it was not. The matching rule
was documented in [`domain/term_frequency.py`](../src/doc_ray/domain/term_frequency.py)
and stated to the caller only *after* a zero-hit result — never in the tool
description, which is the one text an agent has already read when it interprets a
result. It now leads the description, and `test_mcp_p0.py` fails the build if it
stops doing so.

Stating the rule also meant keeping it. Three ways the implementation did not:

| Symptom | Cause | Fix |
|---|---|---|
| `Hick's law` → 0 hits, reported as an *exhaustive* scan | The corpus stores the typographic `’` that PDF/EPUB extraction preserves; the caller types `'` | Confusable folding: `'`↔`’`, `-`↔`–`/`—`, `"`↔`“`/`”` |
| `shared_buffers` also matched `sharedXbuffers` | `_` and `%` reached Postgres as LIKE wildcards — on exactly the term class BL-164 redirects here | `%`/`_`/`\` escaped, `ESCAPE '\'` stated explicitly |
| A hit found case-insensitively came back with an empty `hit` window | The KWIC split was `str.partition` (case-**sensitive**) while the match was `ILIKE` (case-**insensitive**) | One fold-aware splitter, shared |

The rule lives in [`domain/search_terms.py`](../src/doc_ray/domain/search_terms.py)
and is rendered to SQL by [`adapters/sentence_search_sql.py`](../src/doc_ray/adapters/sentence_search_sql.py);
the API/MCP read adapter and the webapp's keyword route both use it, so a reader
and an agent searching the same string get the same rows. Two properties keep it
cheap and honest: the fold is **1:1 and length-preserving**, so a folded offset is
an offset into the original text and `charStart`/`charEnd` stay exact; and a term
with no confusables expands to exactly one pattern, so the `gin_trgm_ops` index
(migration 002) is used exactly as before. A term with three or more confusables
would exceed the 16-pattern ceiling and falls back to folding the column with
`translate()` — correct, index-degraded, and rare.

Phrase mode composes rather than replaces that rule. The shared
`phrase_candidate_clause` ANDs parameterized per-word `match_clause` predicates
(longest ≥3-character word first), then `surface_word_v1` verifies boundaries,
joiners, order, and adjacency on the raw Sentence span. One result row remains one
Sentence; `occurrences` lists every absolute half-open span and
`occurrenceCount` gives its size. The first occurrence supplies KWIC. Candidate
work stops at 2,001 and that bound is visible in the result envelope. This stage
adds no table, token dependency, stemmer, model, or environment setting.

Folding widens; it never narrows. `hick` still matches `thick`, because that is
the documented substring rule — the description now says so up front instead of
leaving the caller to infer a lemma pipeline from the evidence.

### 3.1.1 Corpus-wide semantic search is supported, and was not (BL-205)

For a period, `semantic_search_sentences` returned a typed `timeout` for **every**
query that did not pass a `document_id`, while the same query scoped to one
document answered promptly. That made the tool usable only when you already knew
which document you wanted — the inverse of what it exists for — and the guidance
it emitted on failure ("narrow it, pass document_id") described the workaround as
if it were the intended usage.

The cause was the shape of the row-level security policy on `embedding`, not the
vector index and not the query. Expressing visibility as `span_id IN (SELECT id
FROM spans WHERE document_id IN readable)` makes the planner derive ~28M span ids
out of a 113M-row table; it cannot hash a set that large, so it materialised it
and rescanned it for every candidate row the HNSW index produced. Scoping to a
document shrank the candidate set, which is why that path stayed fast — the
policy cost was identical, it was simply paid fewer times.

Migration 157 states the same rule as a correlated scalar subquery on the `spans`
primary key against a once-materialised readable-document array. Same visibility
(`spans.id` is unique, so both forms admit exactly the same rows), 0.20 s instead
of a >90 s timeout. **No client change is required**, and nothing needs
re-embedding: pass `query` with no `document_id` and it works.

```jsonc
// Corpus-wide: previously always timed out, now the normal path.
{"name": "semantic_search_sentences",
 "arguments": {"query": "evaluating LLM reliability in production", "limit": 5}}

// Still supported, and still the right call when you know the scope.
{"name": "semantic_search_sentences",
 "arguments": {"query": "spice harvesting", "document_id": "…", "limit": 20}}
```

A `timeout` from this tool is therefore no longer an expected outcome of a broad
query. If you see one, it is a real fault worth reporting rather than a hint to
narrow the search.

### 3.1.2 An empty semantic ranking is a precondition failure (BL-166)

`semantic_search_sentences` scoped to a document returned `{"result": []}`, which
a reviewer recorded as "no semantically similar sentences in this book". The book
had no vectors. The ranking applies **no similarity floor** — it orders every
embedding in scope by cosine distance and lets `LIMIT` cut the tail — so zero rows
can only mean *nothing was searched*, never *nothing was similar*. Embedding is
opt-in per document (`embed_document_sentences` is a worker job, never
auto-chained after ingest), and a model cutover leaves prior-model vectors
invisible behind the `e.model` filter, so an unembedded scope is the normal case,
not an edge one.

The tool now fails typed rather than returning an empty list:

```jsonc
// → [not_found] No embedded sentences in scope: document 9f14… has no sentence
//    vectors for the active embedding model. This ranking applies no similarity
//    floor, so this is NOT a finding that nothing was semantically similar —
//    nothing was searched. … — run embed_document_sentences(document_id=…) …
```

The typed error is the *agent* boundary only. `briefing_pack` composes over the
`BriefingReader` port, whose `_probe_semantic` already distinguishes four states
(`off` / `unavailable` / `no_vectors` / `on`) and reports the sharper gap — "No
sentence vectors are visible in your corpus yet; ranked passages by
full-text/keyword match instead." Collapsing an unembedded scope into
`SemanticUnavailable` would have *downgraded* that message, so the MCP reader
keeps returning an empty page to the port while raising for the agent. The
distinction is the point: a program branches on state, a caller writing prose
quotes it.

That asymmetry is also why the defect survived. Both non-agent surfaces already
handled it — the port with a four-state probe, the webapp by falling back to
keyword results with a notice. Only the agent surface returned the bare `[]`.

**A statement timeout is a scope problem.** Corpus-wide semantic search can
exhaust the API pool's `statement_timeout` (`DOCRAY_API_STATEMENT_TIMEOUT_MS`,
default 15 s). That arrived as an unclassified `upstream_error`, which reads as
"the server is broken" rather than "this query was too wide"; it is now a typed
`timeout` whose hint asks for `document_id`, a lower `limit`, or a ranged read.
The typing is not a performance fix — see BL-167 for the measurement.

### 3.2 `read_document_outline` — the map that aims the ranged reader (BL-146)

Random access is only as good as the agent's ability to guess an ordinal. The
outline supplies them: a short list of regions whose `ordinalStart`/`ordinalEnd`
go straight back into `read_document_sentences`, and whose `pageStart` feeds the
`page` reader.

```jsonc
{"name": "read_document_outline", "arguments": {"document_id": "1b7dcdd2-…", "max_entries": 8}}
// → {"source": "headings", "totalSentences": 10063, "entryCount": 8,
//    "note": "Structure from 8 detected headings. … 62 deeper heading(s) omitted …",
//    "entries": [
//      {"level": 1, "title": "Princess of Dune", "ordinalStart": 0,    "ordinalEnd": 945,   "pageStart": 7},
//      {"level": 2, "title": "8",                "ordinalStart": 946,  "ordinalEnd": 2206,  "pageStart": 50},
//      … {"level": 2, "title": "58", "ordinalStart": 8496, "ordinalEnd": 10062, "pageStart": 366}]}

// "read the ending" is now the last entry's range, not a guess:
{"name": "read_document_sentences",
 "arguments": {"document_id": "1b7dcdd2-…", "from_ordinal": 8496, "to_ordinal": 10062,
               "response_format": "concise"}}
```

**`source` is the honest field, and it must be read before the titles are.**

| `source` | Meaning | Safe to do |
|---|---|---|
| `headings` | Structure detected in the document | Aim reads; cite titles as headings |
| `uniform` | An even split of the sentences | Aim reads. **Do not** quote the titles as chapter names — they are the sentence each block opens on |
| `empty` | Nothing to map (unknown, unreadable, or no sentences yet) | Nothing |

Why the fallback exists: heading detection is heuristic — markup for
EPUB/DOCX/MD, relative font size for PDFs ([`pdf_layout.py`](../src/doc_ray/domain/pdf_layout.py)) —
and it is uneven in exactly the wrong direction. Measured on the live corpus it
finds 723 headings in a technical book, 70 in *Princess of Dune*, 17 in *Dune*,
and **none at all** in *Dune Messiah*. A tool that answered `[]` for a novel
would fail precisely the documents this exists for, so a heading-less document
gets an even grid that says so rather than an empty map that looks like an
answer.

When a document has more headings than `max_entries`, the deepest levels are
dropped rather than the tail of the document — mapping the preface and calling it
the book is worse than mapping coarsely. One refinement earned in production:
*Princess of Dune* carries a single level-1 heading (its title) and 69 level-2
chapters, so "keep level 1" produced one entry spanning all 10,063 sentences — an
outline that maps nothing. A level that fits but cannot fill the budget is now
passed over in favour of an even sample of the next level down. Whatever is
dropped is stated in `note`; the map is never silently truncated.

### 3.3 `entity_profiles` — reconciled identities, disagreement included (BL-146)

`entity_frequency` returns raw NER: one row per (surface, type). That splits a
single real-world entity across rows and leaves the agent to merge them by hand —
the reviewer who noticed "Duncan Idaho became a person and a state" was doing the
tool's job for it. `entity_profiles` returns one row per entity:

```jsonc
{"name": "entity_profiles", "arguments": {"limit": 50}}
// → [{"surface": "Arrakis", "mentions": 1421, "dominantType": "LOC",
//     "typeSpread": [{"entityType":"LOC","count":514}, {"entityType":"GPE","count":495},
//                    {"entityType":"PERSON","count":209}, {"entityType":"NORP","count":179},
//                    {"entityType":"ORG","count":24}],
//     "typeConsistency": 0.36, "surfaceForms": ["Arrakis"],
//     "canonicalId": null, "canonicalName": null, "resolved": false}, …]
```

**`typeConsistency` is arithmetic, not a model score** — the dominant type's share
of the entity's mentions. 1.0 means the annotator never contradicted itself; 0.36
means it contradicted itself constantly and the type is not to be relied on.
Corpus-wide, 47 of the top 200 entities score below 0.9. That is not noise to be
hidden; it is the measurement, and surfacing it is [PALS's Law](../CLAUDE.md)
applied at the read surface — NER output presented as the untrusted, reconciled
thing it is.

**`resolved`** says whether the entity is backed by the canonical registry
(`form_index` → `canonical`). `false` means the row is reconciled NER and nothing
more. Be aware of the substrate: this corpus currently holds 3 canonical rows
against 67k mentions, so almost everything is `resolved: false` until the
canonicalization pipeline is run over it. The field exists so that fact is
visible rather than implied.

Only normalization-equivalent spellings fold together, via the same
[`form_norm`](../src/doc_ray/domain/canonical_norm.py) key `form_index` uses.
`Duncan` and `Duncan Idaho` stay separate rows: relating them is coreference, and
guessing it would be fabrication.

### 3.4 `response_format` — the token lever on the bulk readers (BL-146)

`read_document_sentences` and `search_sentences` accept
`response_format: "detailed" | "concise"`. `detailed` is the default and the
historical shape, so nothing that exists today changes behaviour.

`concise` drops the row id and the per-row cursor — only the last cursor of a page
is ever used to continue, so the last row keeps one — and, for a
document-scoped search, the redundant title and `documentId`. **Measured: 47,314 →
29,876 characters on a realistic 200-sentence page, a 37% reduction.** (That is
our number on our payloads; Anthropic report ~⅔ on different tooling, which does
not transfer.) The saving is taken on the wire too — the tool asks GraphQL for a
smaller selection set, it does not fetch and discard.

What `concise` never drops is attribution: `ordinal`, `charStart`, and `charEnd`
survive in every mode, because a cheaper answer that cannot be cited is not a
cheaper answer.

### 3.5 `list_lenses` — the entity-resolution scopes (Block B)

A **lens** is a `document_collection` marked `isScope`: a *named scope* under
which a later projection layer will interpret entity identity, so the fictional
*Duncan Idaho* of one novel stays distinct from an unrelated book's Duncan. The
reserved **`global`** lens holds real-world (Wikidata-linkable) identities shared
corpus-wide; scope lenses are the fictional/work-local scopes beside it.
`list_lenses` is `list_collections` narrowed to those rows.

```jsonc
{"name": "list_lenses", "arguments": {"limit": 50}}
// → [{"id": "…", "name": "global", "isScope": true, "parentLensId": null, "documentCount": 0},
//    {"id": "…", "name": "Dune Series", "isScope": true, "parentLensId": null, "documentCount": 6}]
```

`documentCount` is scoped to the documents your token can see; the lens rows
themselves are shared corpus navigation metadata. Block B ships the vocabulary
only — nothing about how entities resolve changes yet (a later projection block
consumes it).

### 3.5.1 `entity_projection` — lens-scoped identity (Block D)

The projection engine makes lens-scoped identity real: `entity = project(mention,
lens)` is a *derived* interpretation, so the same corpus holds fiction and
non-fiction without a lossy merge. `entity_projection` answers "which entity is
each mention, within this lens". Omit `lens_id` to use the document's default lens,
else `global` — so an existing reader is unchanged. Every machine projection is
`proposed` until a human confirms it with `confirm_projection` (PALS's Law); the
substrate is never mutated.

```jsonc
// Who is each mention in this novel, read through its work lens?
{"name": "entity_projection", "arguments": {"document_id": "…", "lens_id": "…"}}
// → {"lensId": "…", "isDefault": true, "ambiguous": false,
//    "entities": [{"mentionId": "01J…", "scope": "local", "status": "proposed",
//                  "canonicalId": 8842, "surface": "Duncan", "preferred": "Duncan"}]}

// Confirm one — a proposal becomes a decided identity (worker applies it):
{"name": "confirm_projection", "arguments": {"mention_id": "01J…", "lens_id": "…"}}
// → {"id": "…", "kind": "refresh_projection", "status": "queued"}   // poll with get_job
```

`reject_projection` is the mirror of `confirm_projection`. A projection is derived and
rebuildable, so a wrong one is never load-bearing — drop and re-derive, never a
destructive merge.

### 3.5.1b `lens_entities` / `entity_relations` / `related_entities` — hyper-context (Block F)

Once identity is projected, these precomputed rollups answer "who and what is this lens
about, and how do they relate?" — cheaply, at corpus scale. **Prefer `lens_entities` over
`entity_frequency`** for surveying a lens. Every result carries a `refreshedAt` staleness
stamp; relationship `strength` is an **NPMI co-occurrence measure — a structural signal,
not a claim that two entities are truly related**, and edges must not be chained as
identity.

```jsonc
// The lens's significant cast/places, richest first (with type profile + staleness):
{"name": "lens_entities", "arguments": {"lens_id": "…", "min_mentions": 3, "limit": 50}}
// → [{"canonicalId": 8842, "preferred": "Duncan", "scope": "local", "nMentions": 42,
//     "typeProfile": {"PERSON": 42}, "typeConsistency": 1.0, "firstSeenOrdinal": 12}]

// Who co-occurs with Duncan in this lens, strongest first?
{"name": "entity_relations", "arguments": {"lens_id": "…", "canonical_id": 8842}}
// → [{"canonicalId": 8901, "preferred": "Paul", "cooccurrenceCount": 30, "strength": 0.61}]

// Friend-of-a-friend discovery — bounded to 2 hops (>3 is rejected, never clamped):
{"name": "related_entities", "arguments": {"lens_id": "…", "canonical_id": 8842, "hops": 2}}
// → [{"canonicalId": 8901, "preferred": "Paul", "hops": 1}, {"canonicalId": 9100, "hops": 2}]
```

Read the same corpus through different lenses by passing a different `lens_id` — the
composability is a lens parameter, not a query language.

### 3.5.2 `allusions` / `propose_allusion` — cross-lens links, NOT identity (Block E)

Projection keeps a novel's Paris and the real Paris distinct. An **allusion** is the
honest way to connect them: a typed, directional edge (`alludes_to`, `depicts`,
`references`, `adapted_from`, `inspired_by`) that records the relationship **without
merging the entities**. ⚠ An edge is *not* an equality and is *never transitive* — do
not read one as "these are the same," and do not chain `A→B`, `B→C` into `A→C`. Reading
`allusions` requires visibility of **both** lenses (and the evidence document), so an
edge never reveals an entity in a lens you cannot see.

```jsonc
// The novel's Paris (a work-local entity) alludes to the real Paris (global):
{"name": "propose_allusion", "arguments": {
  "from_canonical_id": 5001, "from_lens_id": "<novel-lens>",
  "to_canonical_id": 84, "to_lens_id": "<global-lens>",
  "edge_type": "alludes_to", "evidence_document_id": "<the-novel-doc>"}}
// → {"id": "…", "edgeType": "alludes_to", "status": "proposed", "method": "human"}

{"name": "confirm_allusion", "arguments": {"allusion_id": "…"}}   // human verification
// → {"id": "…", "status": "confirmed"}

// What does the novel's Paris allude to? (outgoing) / what alludes to the real Paris? (incoming)
{"name": "allusions", "arguments": {"canonical_id": 5001, "lens_id": "<novel-lens>", "direction": "outgoing"}}
```

Machine-proposed edges (including the optional `propose_allusions` batch job) land
`proposed`; only `confirm_allusion` asserts the relationship — and even then it is a
relationship, never an identity.

### 3.6 `term_frequency` — the flood-risk pre-flight (Item 10)

BL-145 shipped the *diagnosis*: after a `search_sentences` call floods, its
`coverage.limit_reached` + `note` explain why. `term_frequency` ships the
*prevention*: ask how common a term is **before** spending the call, and pick a
rarer one for free. It reads the corpus-wide `lexemeStats` rollup (never the
17.5M-row `lexeme_occurrences` fact table — D-024), one indexed statement for the
whole batch.

```jsonc
{"name": "term_frequency", "arguments": {"terms": ["spice", "sandworm", "zephyrquux"]}}
// → [{"term": "spice", "normalizedTerm": "spice", "uses": 41822, "totalUses": 5_000_000,
//     "share": 0.0084, "floodRisk": "high", "attested": true,
//     "note": "'spice' saturates the corpus: a keyword search will return a wall of hits …"},
//    {"term": "sandworm", …, "uses": 2130, "share": 0.00043, "floodRisk": "low", "attested": true, "note": null},
//    {"term": "zephyrquux", …, "uses": 0, "share": 0.0, "floodRisk": "low", "attested": false,
//     "note": "'zephyrquux' is unattested in this corpus — a search for it will most likely return nothing. …"}]
```

**Batch by default** (`terms: [String!]!`): triage every candidate in one call, do
not spend one round-trip per term. Each row carries the numbers behind the verdict
(`uses`, `totalUses`, `share`) so a caller can disagree; `measurement` says whether
that number is `exact`, a `component_upper_bound`, or `unavailable`.
`attested: false` distinguishes an absent lexeme from a lookup failure.

**Whitespace phrases retain conservative secondary evidence.** For `"design
decision"`, the rollup read fetches `design` and `decision`; `uses` is the smaller
component count, because the phrase cannot occur more often than its rarest
component. The row reports `measurement: "component_upper_bound"` and includes both
component counts in `parts`, but its primary `floodRisk`/`attested` verdict comes
from the direct sentence scan below. Separate component counts still do not prove
adjacency.

**What this tool cannot count, and why it now says so (BL-164).** The lexeme
tokenizer (`lexemes._WORD_RE`) excludes `_` and digits from word characters, so the
index structurally cannot hold `shared_buffers`, `pg_stat_activity`, `COVID-19`, or
`IPv6` as single entries — it holds `shared` + `buffers`, and truncates `COVID-19`
to `COVID`. The lookup key (`norm_lemma`) does not tokenize, so these terms were
being queried against an index that could not contain them: a guaranteed zero,
previously reported as `attested: false` with "a search will most likely return
nothing". That was a false negative on exactly the rare, specific technical terms
this tool exists to steer callers *toward* — and it contradicted `search_sentences`,
whose literal-substring match finds them without difficulty.

**Since 1.53.1 (#46), candidate counts are verified occurrence counts.** The
lexeme index still cannot hold the term. Multi-word scans reuse phrase search's
parameterized AND-of-words `phrase_candidate_clause`; one-profile-word compounds
such as `shared_buffers` reuse the existing literal `match_clause`. These are
index-backed candidate supersets, not the answer. One `UNION ALL` statement
fetches bounded Sentence ids/text for the whole term batch, and the pure domain
stage verifies boundaries, order, adjacency, joiners, and overlapping starts with
`phrase_profile.find_occurrences`.

The stable flat contract makes the distinction explicit:

- `profile: "surface_word_v1"` names the verification policy;
- `sentenceHits` is the bounded number of SQL candidate sentences;
- `occurrenceSentences` is the candidate sentences with one or more verified
  occurrences;
- `occurrences` is the total verified, overlap-aware occurrence count.

The invariant is `occurrences >= occurrenceSentences <= sentenceHits`.
`attested` and `floodRisk` use `occurrenceSentences`, never `sentenceHits`, so
`algorithm designed` can be a cheap candidate for `algorithm design` without
becoming false attestation or a false flood warning.

```jsonc
{"name": "term_frequency", "arguments": {"terms": ["Model Context Protocol", "shared_buffers"]}}
// → [{"term": "Model Context Protocol", "method": "sentence_scan",
//     "profile": "surface_word_v1", "floodRisk": "moderate",
//     "sentenceHits": 520, "occurrenceSentences": 412, "occurrences": 447,
//     "totalSentences": 1500000, "capped": false, "attested": true,
//     "uses": 500, "measurement": "component_upper_bound",   // secondary evidence
//     "parts": [{"part": "model", "uses": 900}, …],
//     "note": "'Model Context Protocol' has verified surface_word_v1 occurrences
//              in 412 of 1500000 sentences — more than one page …"},
//    {"term": "shared_buffers", "method": "sentence_scan", "floodRisk": "low",
//     "profile": "surface_word_v1", "sentenceHits": 40,
//     "occurrenceSentences": 37, "occurrences": 42, "attested": true,
//     "uses": null, "share": null,        // no lexeme count exists, so none is invented
//     "parts": [{"part": "shared", "uses": 12}, {"part": "buffers", "uses": 70}]}]
```

**`totalUses` is a maintained scalar, not a live sum.** The `share` a term
represents divides its `uses` by `corpus_stats.total_lexeme_uses`, a per-tenant
rollup column maintained by the refresh functions — never a
`SUM(uses) FROM lexeme_stats` on the request path. That aggregate is what the tool
used to do, and forced RLS evaluates the policy per row across the whole table:
2.0 s as `docray_batch` (BYPASSRLS) against 22.9 s on the real `docray_api` path
(production corpus, 2026-07-30, 444,294 rows), so every call exceeded the 15 s
statement timeout and returned a typed `timeout` error — single ordinary words
included. If you are running against a database older than schema 1.56.0, or one
where the rollup has never been refreshed, `totalUses` reads `0` and `share` is
therefore meaningless; `uses` itself stays exact, because it is an indexed
per-lemma lookup rather than a corpus-wide aggregate. Operators repair the scalar
with `SELECT docray_backfill_total_lexeme_uses();` (see §3.6).

**The denominator is nullable by design.** `totalSentences` comes from the
maintained `corpus_stats` rollup, not a live count over the large `sentences` fact
table. The `uses`/`share` denominator is maintained the same way, reading
`corpus_stats.total_lexeme_uses` rather than summing `uses` over `lexeme_stats`
per request — under forced RLS that sum was evaluated per row across the whole
table and exceeded the API statement timeout, so the tool failed for every term.
If the scan measures positive hits while that rollup is missing or stale,
the response keeps the measured hit count and absolute hit-band verdict but returns
`totalSentences: null` with an explicit note:

```jsonc
// The same shape is returned by GraphQL termFrequency and MCP term_frequency.
{"term": "Model Context Protocol", "method": "sentence_scan",
 "profile": "surface_word_v1", "sentenceHits": 48,
 "occurrenceSentences": 43, "occurrences": 47,
 "totalSentences": null, "capped": false,
 "floodRisk": "low", "attested": true,
 "note": "… total-sentence denominator is unavailable because corpus_stats rollup is empty; …"}
```

This is an integrity-degraded result, never “43 of 0 sentences.” The `floodRisk`
remains usable because scan bands use the bounded verified-sentence count. A
genuinely empty visible corpus still returns zero hits with `totalSentences: 0`.
Operators should apply migration 146 and run `make prod-check NO_SEND=1`; a failing
`corpus_rollups` check means the maintained tenant total disagrees with its
document-level inputs and must be repaired before deployment is declared healthy.

**`capped` makes all three counts floors, not totals.** Candidate fetching stops
at `SENTENCE_HIGH_HITS`, so a phrase that floods the corpus costs no more than
the cap. When `capped: true`, `sentenceHits`, `occurrenceSentences`, and
`occurrences` are each lower bounds: unseen candidates may add verified
sentences and occurrences. Treat the verdict as high/incomplete even when many
fetched candidates are rejected. If none verifies, `attested` is null: unseen
candidates may still match, so `false` would be an unsupported absence claim.

**Sentence-scan bands.** Keyed to verified `occurrenceSentences`: **`low`** below
`SENTENCE_PAGE_SIZE` (200 — the verified result fits one page), **`moderate`** up to
`SENTENCE_HIGH_HITS` (2001 — ten pages), **`high`** at or beyond it, or whenever the
count was capped. Like the share thresholds these are parameters, not law: they live
in `doc_ray.domain.term_frequency` and the payload exposes `sentenceHits`,
`occurrenceSentences`, `occurrences`, and `totalSentences` so a caller can
recompute. `SENTENCE_PAGE_SIZE` mirrors the
platform's `MAX_PAGE_SIZE` and a contract test keeps the two equal — re-tune it only
by changing the page cap, or the "fits in one page" meaning is lost. **To re-tune
`SENTENCE_HIGH_HITS`**, measure how many pages a caller will realistically read
before switching to ranged reading, and keep the cap equal to the `high` threshold
so counting never continues past a decided band.

A term that even the scan cannot serve keeps `indexable: false`,
`floodRisk: "unknown"`, `uses`/`share`/`attested` all `null`, and the BL-164 note
redirecting to `search_sentences` — an honest decline, unchanged.

A well-formed word the index *can* hold, with zero uses, still reports
`attested: false` with the "unattested" note — the real absence signal is unchanged.
Making compounds countable would mean changing `_WORD_RE` and re-annotating the
corpus; that is a migration-scale decision, deliberately not taken here. What is
fixed is the honesty of the pre-flight about what it cannot measure.

**`floodRisk` thresholds.** The verdict buckets the term's `share` of the scope's
total lexeme uses: **`high`** at `share ≥ 0.005` (0.5%), **`moderate`** at
`share ≥ 0.0005` (0.05%), **`low`** below. These cut points flag corpus-*saturating*
terms — the function words plus a handful of top content lemmas clear `high`; common
content words land in `moderate`; scene-specific vocabulary sits in `low`. They are
parameters, not law: they live in `doc_ray.domain.term_frequency`
(`HIGH_SHARE`/`MODERATE_SHARE`), and the payload exposes the raw numbers. **To
re-tune against a live corpus**, compute the distribution of `uses / total_uses`
over `lexeme_stats` and move the cut points so roughly the top ~1% land at `high`
and the next ~9% at `moderate`; record the measured distribution when you do.

**Normalization — what this measures vs. what search matches (FR-5).** The count is
the **lexeme-lemma** frequency, keyed on `norm_lemma` (NFC + casefold) — the exact
normalization the lexeme writer applies (`persistence.get_or_create_lexeme`), so the
number describes the term the lexeme layer indexed. That is deliberately **not
identical** to what the two search tools match, and the difference is a finding, not
a bug:

| Surface | Matches on | Example |
|---|---|---|
| `term_frequency` | lexeme lemma (`norm_lemma`) for a single word; **`surface_word_v1` verified occurrences** for phrases/compounds (`sentence_scan`) | `running` → lemma `run`; `design decision` rejects `design decisions` and counts adjacent whole words |
| `search_sentences` | default: **case-insensitive literal substring**; opt-in `phrase`: `surface_word_v1` whole-word adjacency | substring `run` matches `overrun`; phrase `ngram` rejects `Tangram` |
| `full_text_search` | **Snowball stem** (`plainto_tsquery`, per-doc config) | `running` → stem `run` |

So `term_frequency` is a **corpus-saturation signal**, a strong proxy for — not an
exact predictor of — any one tool's hit count. It is most precise for base-form
content words (surface ≈ lemma); phrase/compound scans use the same verified
profile as `search_sentences(match="phrase")`, while `sentenceHits` remains
candidate evidence and `uses` remains a component upper bound;
single lexemes diverge for inflected or substring queries;
when in doubt, trust the exposed numbers over the bucket. **Corpus-wide only for
now** (Open decision 1(a)): passing `documentId`/`document_id` returns a typed
`unsupported` error rather than corpus numbers mislabelled as document-scoped — a
`document_lexeme_stats` rollup is the follow-up if the evaluation harness shows
document-scoped triage matters.

### 3.7 Prompts (always registered) — MCP's user-controlled primitive (Item 8)

MCP defines three primitives by *who controls them*: **tools** are model-controlled,
**resources** are application-controlled, and **prompts** are **user-controlled** —
a person picks one from their client's menu. doc-ray ships the corpus methodology —
the outline-then-ranged-read path, search-then-verify, profiles-over-frequency — as
four selectable, argument-taking workflows, so the lesson lives in the server rather
than being re-derived per session (the BL-145 thesis, applied to the primitive a
*user* invokes deliberately). Each returns a single user-role message; each
invocation emits one audit line, like every other caller action. They are read-only
and hold no pool — they call the read tools, inheriting token forwarding and RLS.

| Prompt | Arguments | What the plan carries | Size¹ |
|---|---|---|---|
| `summarize_document` | `document_id` (required) | **Eager**: fetches the real title, sentence count, and outline, then names the exact ordinal ranges to read with `response_format="concise"`, the rule that every claim cites an `ordinal`, and — honestly — whether the outline is `headings` or a `uniform` grid whose titles are **not** chapter names | ~0.9 KB² |
| `ground_a_claim` | `claim` (required), `document_id` (optional) | The search-then-verify path: rare-term `search_sentences`, read the surrounding ordinal range, quote verbatim with offsets; the non-negotiable rule that an ungrounded claim is reported **unsupported**, never asserted | ~0.77 KB |
| `survey_entities` | `document_id` (optional) | Use `entity_profiles` not `entity_frequency`; how to read `typeConsistency` (a measured share, not a model score); corroborate a contested entity before asserting its type; `resolved:false` means no registry backing | ~0.95 KB |
| `sweep_the_corpus` | `shard_count` (optional, default 8) | The map-reduce plan over `corpus_manifest` / `corpus_shard`, including the coverage proof: Σ per-shard document counts **must** equal `totalDocuments`, or a shard was dropped | ~0.76 KB |

¹ Measured length of the returned message with representative inputs. A prompt
message is context the model pays for on every turn it is used, so each is kept well
under ~1.5 KB of prose. ² `summarize_document` grows ~40 chars per outline entry, so
a document with a very deep outline is larger; the ranges are the payload.

**Fetch a prompt** (MCP `prompts/get`) — the client sends the name + arguments:

```jsonc
{"name": "summarize_document", "arguments": {"document_id": "1b7dcdd2-…"}}
// → one user message, e.g.:
//   Summarize "Dune" (4200 sentences). Work from retrieval, not memory: every claim …
//   Coverage plan — read these ranges in order with read_document_sentences(…, response_format="concise"):
//     - Book One: Dune: ordinals 0–1399
//     - Book Two: Muad'Dib: ordinals 1400–2799
//     …
//   This is a `headings` outline built from detected structure …
```

**Honesty propagates.** On a heading-less document `summarize_document` returns a
`uniform`-labelled plan that explicitly instructs the model not to quote the grid
titles as chapter names — the same guarantee `read_document_outline` makes. **Failure
is graceful**: a malformed `document_id` is rejected (`bad_input`) before any network
call; a well-formed but unreadable id returns a plain "not readable — use
`list_documents`" message with no fabricated plan; and if the outline can't be
fetched, the plan degrades to one that tells the model to call `read_document_outline`
itself and says it degraded — it never invents ranges.

### 3.8 Ops surface — health, readiness, and rate limiting (Ops Phase 3, #18)

The MCP service carries the same operational surface as the GraphQL API, on the
same paths and with the same response shapes (`entrypoints/api/health.py` is the
model). All of it is **unauthenticated and information-free** — the probes are
called by the container runtime, which holds no token — and both probes bypass
`BearerAuthMiddleware` **and** the rate limiter via `health.PUBLIC_PATHS`.

| Route | Auth | Behaviour |
|---|---|---|
| `GET /health` | none | **Liveness.** `200 {"status":"ok","version":…}` whenever the process runs. Never calls the network — it must stay green when the upstream is down, or the orchestrator would kill a healthy process. `version` is the value the MCP handshake already advertises; nothing else is disclosed. |
| `GET /readyz` | none | **Readiness.** `200` only when the config loaded **and** the GraphQL upstream answers; otherwise `503` with `{"ready":false,"checks":{…}}` naming the failed check. The upstream probe is a ≤2 s GET to the API's own `/health`; a **negative** result is cached ~5 s so a readiness storm cannot amplify an upstream outage. Failures carry an error *kind* only — never the upstream host, URL, or a stack trace. |

```bash
curl -fsS localhost:8080/health   # → {"status":"ok","version":"1.48.0"}
curl -sS -o /dev/null -w '%{http_code}\n' localhost:8080/readyz   # 200 ready, 503 if the API is unreachable
```

The compose `healthcheck` hits `/health`, so `depends_on: condition: service_healthy`
is meaningful for anything layered on `mcp`.

**Rate limiting.** A per-principal fixed-window limiter (`entrypoints/mcp/ratelimit.py`)
wraps the shared `doc_ray.domain.rate_limit` core — the *same* implementation the
webapp uses, not a second one. The key is a non-reversible fingerprint of the bearer
token the auth layer resolved (falling back to the client IP for anonymous callers,
which in practice only reach the exempt probes). On breach it returns `429` with a
`Retry-After` header and logs the rejection on `doc_ray.mcp.ratelimit` by fingerprint
— never the token. Configure with `DOCRAY_MCP_RATE_LIMIT_REQUESTS` (default `120`) and
`DOCRAY_MCP_RATE_LIMIT_WINDOW_SECONDS` (default `60`); set requests to `0` to disable.
The default is generous — it only catches runaway loops.

**Transport reuse and compression.** All tool calls share one process-wide
HTTPX connection pool instead of reopening TCP/TLS on each GraphQL hop. The pool
never stores a caller token; authorization remains a per-request header.
`DOCRAY_MCP_POOL_MAX_CONNECTIONS` (default `100`) and
`DOCRAY_MCP_POOL_MAX_KEEPALIVE` (default `20`) bound it, and ASGI lifespan
shutdown closes it after FastMCP's own session manager exits. Responses of at
least 1 KiB are gzip-compressed when requested, while `text/event-stream` is
explicitly excluded so MCP streams keep flushing incrementally.

**Restart and replica behavior.** MCP transport sessions are stateless. A client
may initialize against one process and send the next request to another, or
continue after a container restart, without presenting a stale process-local
session id. This is safe because the server stores no conversation, task, or
authorization state between calls: each request is authenticated, and durable
application state lives behind GraphQL/Postgres. Stream resumability/event replay
is not advertised; no server operation currently depends on it.

> **Rate-limit state is per-process** (Open decision 2). The MCP service runs as **one**
> container, so this is correct as deployed; if it is ever scaled to *N* replicas the
> effective limit is *N×* the configured value (each process counts independently).
> A shared store (Redis/Postgres) would be needed for a hard cluster-wide cap — not
> introduced here, deliberately.

### Write tools (opt-in — `DOCRAY_MCP_ENABLE_WRITE=true`, served at `/mcp/curate`)

Registered **only** when writes are enabled, and only on the curation endpoint
`/mcp/curate` — a disabled deployment exposes no such path at all, and an enabled
one still serves `/mcp` read-only (§2.1). Each
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
`get_job`. See [`mutations.py`](../src/doc_ray/entrypoints/mcp/mutations.py),
[`mutations_surface.py`](../src/doc_ray/entrypoints/mcp/mutations_surface.py),
and engineering decision **D-030**.

| MCP tool | GraphQL mutation | Returns |
|---|---|---|
| `ingest_document(ref, title?, pipeline=True)` | `ingestDocument` | `Job` (the only intake verb; MCP has no file transport, so a reference is an agent's only route into the corpus) |
| `reannotate(document_id, lang="en")` | `reannotate` | `Job` |
| `reextract_document(document_id)` | `reextractDocument` | `Job` |
| `annotate_document(document_id, lang="en")` | `annotateDocument` | `Job` |
| `semantic_enrich_document(document_id)` | `semanticEnrichDocument` | `Job` |
| `embed_document_sentences(document_id)` | `embedDocumentSentences` | `Job` (learned Layer-20 embed; worker sentence stack required) |
| `canonicalize_document(document_id, resolve=False)` | `canonicalizeDocument` | `Job` (LLM only if `resolve`) |
| `suggest_seeds(document_id, limit=40)` | `suggestSeeds` | `Job` |
| `suggest_document_metadata(document_id)` | `suggestDocumentMetadata` | `Job` |
| `update_document_metadata(document_id, title?, description?, tags?, authors?)` | `updateDocumentMetadata` | `Job` |
| `bulk_tag_documents(document_ids, tags)` | `bulkTagDocuments` | `[Job]` |
| `correct_entity_mention(document_id, action, char_start, char_end, entity_type?, surface?, new_char_start?, new_char_end?, original_type?, rationale?)` | `correctEntityMention` | `Job` (`apply_corrections`) |
| `correct_entity_surface(surface, original_type, action, document_id?, collection_id?, entity_type?, rationale?, dry_run=True)` | preview: `entitySurfaceOccurrences`; commit: `correctEntitySurface` | impact preview; `[Job]` only when `dry_run=False` |
| `classify_document(document_id)` | `classifyDocument` | `Job` (`classify_genre`) |
| `confirm_document_classification(document_id, genre, subtype?, rationale?)` | `confirmDocumentClassification` | the confirmed `DocumentClassification` |
| `confirm_projection(mention_id, lens_id)` | `confirmProjection` | `Job` (`refresh_projection`) — promotes a projection to `confirmed` |
| `reject_projection(mention_id, lens_id)` | `rejectProjection` | `Job` (`refresh_projection`) — marks a projection `rejected` |
| `propose_allusion(from_canonical_id, from_lens_id, to_canonical_id, to_lens_id, edge_type, evidence_document_id, confidence?, rationale?)` | `proposeAllusion` | the proposed `AllusionEdge` (a link, NOT an identity) |
| `confirm_allusion(allusion_id)` | `confirmAllusion` | the `confirmed` edge |
| `reject_allusion(allusion_id)` | `rejectAllusion` | the `rejected` edge |
| `decide_seeds(approve_ids?, reject_ids?)` | `decideSeeds` | `Int` (rows updated) |
| `decide_document_metadata_suggestions(approve_ids?, reject_ids?)` | `decideDocumentMetadataSuggestions` | `Int` |
| `decide_visual_captions(approve_ids?, reject_ids?)` | `decideVisualCaptions` | `Int` (rows updated) |
| `retract_visual_caption(caption_id, reason)` | `retractVisualCaption` | `Int` (rows retracted; `0` = not committed) |
| `seed_approved(document_id)` | `seedApproved` | `Job` |
| `create_collection(name, description="", kind="manual", rule?, doc_type?, status?, tags?)` | `createCollection` | `DocumentCollection` |
| `add_document_to_collection(collection_id, document_id)` | `addDocumentToCollection` | `DocumentCollection` |
| `remove_document_from_collection(collection_id, document_id)` | `removeDocumentFromCollection` | `DocumentCollection` |
| `update_collection(collection_id, name?, description?)` | `updateCollection` | `DocumentCollection` |
| `delete_collection(collection_id)` | `deleteCollection` | `Boolean` |
| `promote_collection_to_lens(collection_id, parent_lens_id?)` | `promoteCollectionToLens` | `DocumentCollection` |
| `demote_lens(collection_id)` | `demoteLens` | `DocumentCollection` |
| `set_document_default_lens(document_id, lens_id?)` | `setDocumentDefaultLens` | `Document` |

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
when the entity is real but its boundaries are wrong. `adjust_span` also accepts
`entity_type` to retype the mention in the same correction.

For a repeated error, use `correct_entity_surface` with exactly one
`document_id` or `collection_id`. It first calls the bounded
`entitySurfaceOccurrences` preview and defaults to `dry_run=True`; set
`dry_run=False` explicitly to record `reclassify_surface` or `reject_surface`
and enqueue one `apply_corrections` Job per Document. Matching is exact and
case-sensitive on `surface` plus the current `original_type`. Collection scope
is rejected when membership reaches the unpageable 200-member bound or when
`documentCount` does not prove the returned member list is complete.

`classify_document` / `confirm_document_classification` are the genre half of the
verification layer (SLP Block C). A document's genre decides whether its entities
resolve inside the work (fiction) or against the shared world (non-fiction), so a
wrong genre would later drive a wrong identity merge — which is why a machine guess
is only ever `pending` and a human confirms it. A `pending` classification must
**not** be relied on for identity decisions.

**Example — classify a document, then confirm the genre:**

```jsonc
// Enqueue the heuristic classifier (no model — deterministic signals).
{"name": "classify_document", "arguments": {"document_id": "f0b3d10c-…"}}
// → {"id": "01K…", "kind": "classify_genre", "status": "queued", "documentId": "f0b3d10c-…"}

// Poll it, then read the machine guess.
{"name": "get_job", "arguments": {"job_id": "01K…"}}
// → {"status": "succeeded", "result": {"genre": "fiction", "status": "pending", "written": true}}
{"name": "document_classification", "arguments": {"document_id": "f0b3d10c-…"}}
// → {"genre": "fiction", "subtype": "novel", "confidence": 1.0, "method": "heuristic", "status": "pending"}

// Having judged it, confirm the genre — this asserts it (method=human, status=confirmed).
{"name": "confirm_document_classification", "arguments": {
   "document_id": "f0b3d10c-…", "genre": "fiction", "subtype": "novel",
   "rationale": "first-person novel with chapter structure"}}
// → {"genre": "fiction", "status": "confirmed", "method": "human", "reviewer": "…"}
```

The three lens tools apply **synchronously** (like the collection tools) and
return the updated `DocumentCollection` / `Document`, not a `Job`. Promoting a
collection to a lens is a **curation decision** — mark a collection a scope only
when its documents share an identity world (a series, a franchise, one novel);
an ordinary collection stays identity-neutral. The database enforces the rules:
a default lens must be a scope the document belongs to, a parent lens must itself
be a scope, the parent graph may not cycle, and `global` cannot be renamed,
demoted, or deleted.

**Example — promote a collection, set a document's default lens, read it back:**

```jsonc
// Promote an existing collection (a curated set of one series' books) to a lens.
{"name": "promote_collection_to_lens", "arguments": {"collection_id": "01JADR…"}}
// → {"id": "01JADR…", "name": "Dune Series", "isScope": true, "parentLensId": null, ...}

// A member document can name it as its default lens.
{"name": "set_document_default_lens",
 "arguments": {"document_id": "01JADQ…", "lens_id": "01JADR…"}}
// → {"id": "01JADQ…", "title": "Dune", "defaultLensId": "01JADR…"}

// The scope shows up in list_lenses; demote_lens would now refuse (in use as a default).
{"name": "list_lenses", "arguments": {"limit": 50}}
```

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

`ingestDocument` is exposed **by reference only** (`ingest_document(ref, …)`):
MCP has no multipart transport, so an agent cannot upload a file, but it can name
one — the worker fetches it. Uploading remains GraphQL-only.

Not exposed via MCP: the sharing mutations (`shareDocument`/`revokeDocumentShare`)
and every destructive/admin verb. These remain available on the GraphQL API
directly.

### Resources — `docray://` URIs and templates (always registered)

Alongside tools, the server declares MCP's **resources** primitive: addressable,
host-cacheable handles a client enumerates and fetches with `resources/read`.
They are **addressing, not capability** — each read goes through the same read
tools, so the caller's bearer token and Postgres RLS scope the result
identically, and a `resources/read` still lands its bytes in the caller's context
(the resource abstraction does not shrink payloads). Served by
[`resources.py`](../src/doc_ray/entrypoints/mcp/resources.py).

> **Concrete vs. template — why both exist.** `resources/list` and
> `resources/templates/list` read **disjoint registries**: a parameterized URI is
> returned *only* by the templates call. A template-only surface therefore makes
> `resources/list` return `[]`, which clients render as "this server has no
> resources" — the defect that shipped in the first cut of this layer. The three
> **concrete** entry points below are parameterless so they are listable and give
> a client somewhere to start; the templates are drill-down targets reached from
> them. `test_mcp_resources.py` asserts `resources/list` is non-empty and that
> every parameterless spec lands in the concrete registry.

| URI / template | Type | Serves |
|---|---|---|
| `docray://corpus/documents` | `application/json` | **concrete** — the document index (id, title, type, counts); start here |
| `docray://corpus/collections` | `application/json` | **concrete** — the collection index |
| `docray://corpus/manifest` | `application/json` | **concrete** — shard map at the default width (8) |
| `docray://doc/{document_id}` | `text/plain` | full text, one sentence per line; truncates **explicitly** at 200 000 chars and names the ranged template for the remainder |
| `docray://doc/{document_id}/sentences/{start}-{end}` | `text/plain` | sentences `start`..`end` (inclusive ordinals) — the bounded, citable slice |
| `docray://doc/{document_id}/pages/{page}` | `text/plain` | every sentence on one source page |
| `docray://collection/{collection_id}` | `application/json` | one collection + its member documents |
| `docray://corpus/shards/{shard_count}` | `application/json` | the corpus shard map (map-reduce plan) |

```jsonc
// resources/list → the three concrete entry points; start from one of them
{"uri": "docray://corpus/documents"}   // → JSON index of documents

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
- [x] **TLS front door — DONE.** Served behind the Cloudflare tunnel via the
      `doc-ray.com /mcp → http://mcp:8080` route (see the Status header above); the
      `mcp` service is loopback-bound on `:8080` and never published raw.
- [x] **Per-agent token minting + rotation runbook — DONE** (#18). See
      `docs/production-setup.md` §"MCP tokens" and §3.8 below; the exact invocation is
      `python -m doc_ray.entrypoints.auth_admin token mint --principal <id> --scopes read`.
- [x] **`/health` + `/readyz` — DONE** (#18). `entrypoints/mcp/health.py`: liveness is
      dependency-free (200 even when the API is down); readiness returns 503 naming the
      failed check. Both bypass `BearerAuthMiddleware` (`health.PUBLIC_PATHS`); the compose
      `healthcheck` hits `/health`. See §3.8.
- [x] **Request rate limiting — DONE** (#18). `entrypoints/mcp/ratelimit.py`, reusing the
      shared `domain.rate_limit` limiter; per-principal, `429` + `Retry-After` on breach,
      `DOCRAY_MCP_RATE_LIMIT_REQUESTS`/`_WINDOW_SECONDS`. See §3.8.
- **Acceptance:** MCP service reachable only via the TLS front; ops runbook section added. ✅

### Phase 4 — v2 (post-first-version, optional)  ·  size: L
- [x] **MCP OAuth 2.1 — DONE** (commit `93c0e99`). Shipped as a full OAuth 2.1
      **Authorization Server** (not just a resource server): RFC 7591 Dynamic Client
      Registration → `/authorize` (browser login + consent, PKCE S256) → `/token`
      (authorization_code + PKCE), reusing the existing RLS-bound access JWTs.
      See `application/oauth_server.py`, `webapp/oauth_web.py`, migration `082`.
- [x] **Write tools — DONE.** `mutations.TOOLS` registers 31 write tools (reprocessing
      + curation + full collection lifecycle + SLP projection/allusion review), advertised only when
      `DOCRAY_MCP_ENABLE_WRITE=true`; each remains gated by the API's `write` scope and
      per-document ACLs (`test_mcp_server.py::_WRITE_TOOL_NAMES`).
- [ ] **gRPC gateway** (if required) — a separate service exposing the same
      `corpus.py` tool functions to gRPC clients; reuses the passthrough unchanged.

## 5. Testing strategy
- **Unit (Phase 1, SDK-independent):** run directly with `.venv/bin/pytest` — no DB,
      no `mcp`. Stub `httpx` and the request object.
- **DB-backed / integration:** none required for v1 (the MCP layer never touches
      Postgres directly); if added later, use `scripts/test-in-docker.sh`.
- **End-to-end (Phase 2):** MCP Inspector or a Claude client against a running stack,
      asserting RLS scoping with two principals (one sees a doc, one doesn't).
- **Evaluation harness (Item 11):** the surface is measured, not asserted. A fixed
      set of grounded retrieval tasks runs against a seeded, disposable fixture
      corpus and reports task success, tool-call count, and payload size, so a change
      to a tool description or return shape can be *shown* to help or hurt. It calls
      the real tool callables and routes their GraphQL over an in-process schema
      (no API server needed). Run it with
      `python scripts/eval_mcp.py --mode scripted [--json <path>] [--baseline <path>]`;
      a committed baseline lives at `docs/eval/mcp-baseline.json`, and a drift guard
      (`test_mcp_eval_tasks.py`) fails the build if any registered read tool is
      exercised by no task. Full guide: [`docs/mcp-evaluation.md`](mcp-evaluation.md).
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
