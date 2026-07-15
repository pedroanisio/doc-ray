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

**Status: live in production and verified end-to-end.** The read-only server
(now **9 tools** — 6 corpus readers + 3 composition-grounding tools from
`entrypoints/mcp/compose.py`) is deployed via `make mcp-up`, routed at
`https://doc-ray.com/mcp` behind the Cloudflare tunnel, and confirmed with a real
MCP client (initialize → tools/list → RLS-scoped tools/call). The **OAuth 2.1
Authorization Server** (originally Phase 4) shipped in commit `93c0e99`
(`application/oauth_server.py`, `webapp/oauth_web.py`, migration
`082_oauth_clients.sql`), so a connector like Claude Desktop obtains a token via
Dynamic Client Registration + PKCE with no pre-shared secret. Sections marked
**DONE** are as-built; unchecked items are roadmap. Remaining: Phase-3 ops polish
(`/health`/`/readyz`) and Phase-4 write tools / optional gRPC.

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

## 2. Module layout

| Module | Depends on | Purpose |
|---|---|---|
| `src/doc_ray/entrypoints/mcp/corpus.py` | httpx only | GraphQL-passthrough client + read-tool functions (SDK-independent) |
| `src/doc_ray/entrypoints/mcp/auth.py` | starlette only | Bearer/JWT validation + `caller_token` contextvar + `BearerAuthMiddleware` |
| `src/doc_ray/entrypoints/mcp/server.py` | `mcp` (new dep) | Thin FastMCP glue: register tools, mount middleware, expose ASGI `app` |
| `docker-compose.mcp.yml` | — | `mcp` service; dev forwards to `http://api:8000/graphql` (in-network), prod to `https://doc-ray.com/graphql` |

Splitting the substance (`corpus.py` + `auth.py`) away from the SDK glue
(`server.py`) means the tools and auth are testable and committable **without** the
new dependency; only `server.py` needs `mcp`.

## 3. Tool inventory (read-only — 9 tools as-built)

The 6 corpus readers below each map to one GraphQL read field
([query.py](../src/doc_ray/entrypoints/api/query.py)); a further **3
composition-grounding tools** (`briefing_pack`, `document_evidence`,
`entity_dossier`) shipped in [`entrypoints/mcp/compose.py`](../src/doc_ray/entrypoints/mcp/compose.py)
for cited-briefing workflows — see that module for their signatures.

| MCP tool | GraphQL field | Notes |
|---|---|---|
| `search_sentences(term, document_id?, limit)` | `searchSentences` | KWIC pre/hit/post |
| `full_text_search(query, limit)` | `fullTextSearch` | ranked docs |
| `list_documents(limit, doc_type?, status?)` | `documents` | keyset `cursor` |
| `get_document(document_id)` | `document` | metadata + counts |
| `entity_frequency(document_id?, entity_type?, limit)` | `entityFrequency` | top entities |
| `lexeme_kwic(uid, document_id?, limit)` | `lexeme { occurrences }` | KWIC is a **nested** resolver, not a root field |

Write tools (`ingestDocument`, `bulkTagDocuments`, `createCollection`, …) are
deferred to Phase 4 behind the `write` scope.

## 4. Phased plan

### Phase 0 — Decisions & prerequisites  ·  **RESOLVED (as shipped in v1)**
- [x] **New `mcp` dependency approved** — `mcp==1.28.*` pinned in `requirements.txt`/`uv.lock`.
- [x] **Transport = Streamable HTTP MCP** (not gRPC); a separate gRPC gateway stays a Phase-4 option.
- [x] **v1 tool set = the 6 read tools above**, read-only scope.
- [x] **Placement** — runs alongside `api` (dev compose) or standalone against the public
      API (prod); both wired in `docker-compose.mcp.yml`.
- **Acceptance:** decisions realized in code (commit `7cd2c4a`).

### Phase 1 — SDK-independent core (no new dependency)  ·  size: M  ·  **DONE**
- [x] `corpus.py` — `gql()` async passthrough (forwards `caller_token`) + the 6 tool functions.
- [x] `auth.py` — `_extract_bearer`, token/JWT validation reusing `cfg.api_tokens` +
      `verify_access_jwt`, `caller_token` contextvar, `BearerAuthMiddleware`.
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
