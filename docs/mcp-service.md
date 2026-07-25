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

**Status: live in production and verified end-to-end.** The server ships **25
read tools** (18 from `entrypoints/mcp/corpus.py` — incl. `read_document_sentences`,
`corpus_manifest`, `corpus_shard`, `get_job`, `list_collections`, `get_collection`,
`entity_mention_corrections`, and the 4 Prism visual tools — + 3 structure tools from
`entrypoints/mcp/structure.py` (`read_document_outline`, `entity_profiles`,
`term_frequency`) + 3 composition-grounding tools from `entrypoints/mcp/compose.py`
+ 1 semantic-search tool… see §3) always registered, plus **22 opt-in write tools**
(reprocessing + curation + full collection lifecycle) registered only when
`DOCRAY_MCP_ENABLE_WRITE=true` — the default surface stays read-only. It also
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
| `src/doc_ray/entrypoints/mcp/structure.py` | httpx only | Structure read tools: `read_document_outline` + `entity_profiles` (split from `corpus.py` for D-015) |
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
| `search_sentences(term, document_id?, limit, response_format?)` | `searchSentences` | KWIC pre/hit/post; term-filtered. Returns `{hits, coverage, note}` — see §3.1 |
| `read_document_sentences(document_id, limit, after?, from_ordinal?, to_ordinal?, page?, response_format?)` | `document { sentences }` | in-order read: **sequential** walk *or* **random-access** ordinal/page slice; verbatim text + citation offsets |
| `read_document_outline(document_id, max_entries)` | `documentOutline` | the map that aims the ranged reader: addressable ordinal ranges + `source` provenance — see §3.2 |
| `full_text_search(query, limit)` | `fullTextSearch` | ranked docs |
| `list_documents(limit, doc_type?, status?)` | `documents` | keyset `cursor` |
| `get_document(document_id)` | `document` | metadata + counts |
| `get_job(job_id)` | `job` | poll a job enqueued by a write tool |
| `entity_mention_corrections(document_id)` | `entityMentionCorrections` | the human NER-correction audit log for a document |
| `list_collections(limit, kind?, after?)` | `collections` | native containers; keyset `cursor` |
| `get_collection(collection_id, document_limit)` | `collection` | one collection + its member documents |
| `list_lenses(limit, after?)` | `lenses` | the collections marked as entity-resolution scopes (Block B); keyset `cursor` — see §3.5 |
| `corpus_manifest(shard_count)` | `corpusManifest` | shard map for a map-reduce sweep: per-shard counts that sum to the total |
| `corpus_shard(shard_index, shard_count, limit, after?)` | `corpusShard` | one deterministic, disjoint slice of the corpus; keyset `cursor` |
| `entity_frequency(document_id?, entity_type?, limit)` | `entityFrequency` | **raw** NER rows, one per (surface, type) — prefer `entity_profiles` |
| `entity_profiles(document_id?, entity_type?, limit)` | `entityProfiles` | one row per entity: merged spellings, measured type spread, `typeConsistency` — see §3.3 |
| `term_frequency(terms, document_id?)` | `termFrequency` | flood-risk pre-flight: `floodRisk` verdict per candidate term BEFORE searching; corpus-wide only (`document_id` → typed `unsupported`) — see §3.6 |
| `lexeme_kwic(uid, document_id?, limit)` | `lexeme { occurrences }` | KWIC is a **nested** resolver, not a root field |
| `semantic_search_sentences(query, document_id?, limit)` | `semanticSearchSentences` | meaning-based (learned-embedding) sentence retrieval; the antidote to lexical `search_sentences` flooding. Typed `unsupported` where the sentence stack is absent |
| `document_images(document_id, limit)` | `document { images }` | Prism visual layer: extracted/rendered page images |
| `image_class_summary(document_id)` | `imageClassSummary` | per-document tally of image tier classes |
| `visual_captions(document_id, limit)` | `visualCaptions` | generated captions for a document's images |
| `cross_modal_image_search(query, limit)` | `crossModalImageSearch` | text→image search; typed `unsupported` where the vision stack is absent |

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
(`uses`, `totalUses`, `share`) so a caller can disagree; `attested: false`
distinguishes an unknown term (a search will return nothing) from a lookup failure.

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
| `term_frequency` | lexeme lemma (`norm_lemma`) | `running` → lemma `run` |
| `search_sentences` | **case-insensitive literal substring** (`ILIKE '%term%'`) | `run` matches `run`, `overrun`, `Brunny` |
| `full_text_search` | **Snowball stem** (`plainto_tsquery`, per-doc config) | `running` → stem `run` |

So `term_frequency` is a **corpus-saturation signal**, a strong proxy for — not an
exact predictor of — any one tool's hit count. It is most precise for base-form
content words (surface ≈ lemma), and diverges for inflected or substring queries;
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
| `embed_document_sentences(document_id)` | `embedDocumentSentences` | `Job` (learned Layer-20 embed; worker sentence stack required) |
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
when the entity is real but its boundaries are wrong.

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

Not exposed via MCP: `ingestDocument` (multipart upload, not a plain mutation),
the sharing mutations (`shareDocument`/`revokeDocumentShare`), and every
destructive/admin verb. These remain available on the GraphQL API directly.

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
- [x] **Write tools — DONE.** `mutations.TOOLS` registers 22 write tools (reprocessing
      + curation + full collection lifecycle), advertised only when
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
