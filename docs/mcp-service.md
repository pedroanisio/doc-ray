---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-15"
---

# MCP Service — Implementation Plan

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Plan for exposing the doc-ray corpus to agentic AI over the **Model Context
Protocol (MCP)**, authenticating each caller to a doc-ray principal so Postgres
RLS scopes what they can read. This is a plan, not an as-built record — nothing
below is implemented yet.

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

## 3. Tool inventory (v1, read-only)

Each maps to one GraphQL read field ([query.py](../src/doc_ray/entrypoints/api/query.py)):

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

### Phase 0 — Decisions & prerequisites (blocking; needs operator)
- [ ] **Approve the new `mcp` dependency** (`mcp==1.*`; v2.x is beta). The repo's
      "no unnecessary dependencies" rule makes this an explicit call.
- [ ] **Confirm transport** = Streamable HTTP MCP (not gRPC). Decide whether a
      separate gRPC gateway is also wanted (Phase 4).
- [ ] **Confirm the v1 tool set** (6 read tools above) and read-only scope.
- [ ] **Decide where it runs** — alongside `api` on the Droplet, or standalone.
- **Acceptance:** the four decisions recorded (here or in `engineering_decisions.yml`).

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
- [ ] **MCP OAuth 2.1** — server as an OAuth *resource server* (metadata discovery +
      dynamic client registration) via FastMCP `TokenVerifier`/`AuthSettings`, so
      third-party clients do a standards login instead of pre-provisioned tokens.
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

## 8. Open decisions (need your input)
1. **Dependency** — OK to add `mcp==1.*`?
2. **Transport** — Streamable HTTP confirmed? Separate gRPC gateway wanted?
3. **Scope** — read-only v1, or include write tools now?
4. **Auth model** — forward caller token (v1) now, OAuth 2.1 resource server (v2) later — or go straight to OAuth 2.1?
5. **Placement** — MCP service co-located with `api` or standalone?

## 9. Sequencing
Phase 0 (decide) → Phase 1 (core, no dep, immediately landable) → Phase 2 (dep +
server + smoke test) → Phase 3 (TLS/ops) → Phase 4 (OAuth/writes/gRPC, later).
Phase 1 can start the moment the tool set is confirmed; Phase 2 needs the
dependency approval.
