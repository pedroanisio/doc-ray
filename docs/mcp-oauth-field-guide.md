---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-15"
---

# Securing an MCP Server with OAuth 2.1 — A Field Guide

**Audience:** engineers building or hardening a Model Context Protocol (MCP)
server that agents (Claude Desktop, Claude Code, custom clients) connect to.
**Goal:** take an MCP server from "paste a bearer token" to a standards-based
OAuth 2.1 sign-in that binds every token to a principal and a specific server —
correctly, and without the silent gaps that make the flow *look* done while
being exploitable.

## How this guide is sourced

Every substantive claim is tagged with where it comes from, because the two
kinds of source have different reliability:

- **[KB]** — grounded in the doc-ray knowledge corpus (books/specs ingested and
  cited by title). Strong on OAuth/OIDC theory and MCP practice.
- **[SPEC]** — the MCP Authorization specification (2025-06) + the underlying
  RFCs (6749/9068 OAuth, 7591 DCR, 7636 PKCE, 8414 AS metadata, 8707 resource
  indicators, 9728 protected-resource metadata). Used where the corpus is
  silent — notably the *discovery* mechanics and the confused-deputy problem.
- **[DR]** — doc-ray's own implementation, used as a worked reference. Every
  `[DR]` claim was verified end-to-end (unit + isolated-database tests) before
  being written here.

Where [KB] and [SPEC] disagree (they do — see §11), the guide reconciles them
explicitly rather than papering over it.

---

## 1. The threat model — why a bearer token is not enough

An MCP server hands an AI agent tools that read (and sometimes act on) real
systems. That makes it a high-value target with agent-specific failure modes:

- **Tool poisoning / unsafe servers.** A malicious or compromised MCP server can
  present tools whose descriptions or behavior subvert the agent. *[KB — "Model
  Context Protocol: Advanced AI Agents for Beginners", Ch. 26 "MCP might be
  risky / Why are MCP servers unsafe?"]*
- **Prompt injection.** Untrusted content flowing through tools can hijack the
  agent's instructions. *[KB — "Principles of Building AI Agents"; the
  Chris Bakke injection, Dec 2023]*
- **Two layers of permission.** Agent systems must separate *who the agent is*
  from *what the agent may do* — authentication and authorization are distinct
  concerns. *[KB — "Principles of Building AI Agents", "Agent authentication and
  authorization"]*
- **Token misuse across servers.** A token minted for one MCP server must not be
  usable against another. *[KB — "Model Context Protocol for LLMs"; [SPEC] RFC
  8707]*
- **Confused deputy.** An MCP server that proxies to a third-party API, or reuses
  a static client id, can be tricked into exercising its own authority on an
  attacker's behalf. *[SPEC — MCP auth spec, "Confused Deputy Problem"; note:
  the corpus does **not** cover this — a real gap you must fill from the spec.]*

A shared bearer token addresses none of these: it is bearer (whoever holds it
is trusted), unscoped (no per-user authorization), and unbound (usable anywhere
it's accepted). OAuth 2.1 replaces it with short-lived, principal-bound,
server-bound tokens obtained through a consented flow.

## 2. The mental model — three roles, one token

*[SPEC + KB]* OAuth for MCP has exactly three roles:

| Role | Who plays it | Responsibility |
|---|---|---|
| **Client** | the MCP client (Claude Desktop) | drives the flow, holds the token, calls the MCP server |
| **Resource Server (RS)** | the **MCP server** | validates the token, serves tools scoped to it |
| **Authorization Server (AS)** | your IdP *or* your own service | authenticates the human, issues tokens |

The AS may be **hosted separately or with the RS** *[SPEC]*. Two common shapes:

- **External IdP.** The MCP server points at Auth0 / Keycloak / Entra ID; you
  only build the RS (validate the JWT in middleware). *[KB — "Learn Model
  Context Protocol with Python": "you would get the token from an identity
  provider (IDP) such as Auth0, Keycloak, or Entra ID … update the middleware to
  validate the JWT."]*
- **Self-hosted AS.** You own login (local/OIDC/SAML) and issue your own tokens.
  More work, but the tokens can bind directly to your row-level authorization.
  *[DR — doc-ray is its own AS, delegating human login to local/OIDC/SAML and
  minting its existing RLS-bound access JWTs.]*

The token itself is a short-lived **bearer JWT** presented in the `Authorization`
header *[SPEC]*; the RS validates it on **every** request (no session). *[SPEC]*

## 3. The flow, end to end

*[SPEC]* The complete first-connect sequence:

```
Client                      MCP server (RS)                 Authorization Server
  |  MCP request, no token        |                               |
  |------------------------------>|  401 + WWW-Authenticate:       |
  |                               |  Bearer resource_metadata=".."|
  |  GET /.well-known/oauth-protected-resource                    |
  |------------------------------>|  { authorization_servers:[AS]}|
  |  GET /.well-known/oauth-authorization-server ---------------->|
  |                               |            { endpoints, S256 } |
  |  POST /register (DCR) ---------------------------------------->|  { client_id }
  |  (generate PKCE verifier+challenge; pick resource)            |
  |  open browser -> /authorize?...code_challenge&resource ------>|  user logs in + consents
  |                               |          302 redirect ?code&state
  |  POST /token  code + code_verifier + resource --------------->|  { access_token, refresh_token }
  |  MCP request + Bearer access_token                            |
  |------------------------------>|  validate aud/exp/sig -> serve|
```

The **loud** parts (DCR, PKCE, token) are well documented in the corpus; the
**quiet** parts that most implementations miss are the two `.well-known`
discovery documents and the `WWW-Authenticate` header — the corpus is silent on
these, so take them from the spec (§5.3).

## 4. Building the Resource Server (the MCP server)

### 4.1 Validate the token on every request
Middleware extracts `Authorization: Bearer <jwt>`, verifies signature + `exp` +
issuer, and rejects anything else with `401`. *[KB — "Learn MCP with Python":
"Update the middleware to validate the JWT"; [DR] `BearerAuthMiddleware` +
`verify_access_jwt`.]* Bind the validated principal into your authorization layer
(doc-ray stamps it into Postgres RLS, so the agent sees exactly what that
principal may read). *[DR]*

### 4.2 Validate the audience — RFC 8707 (the part people skip)
The RS **MUST** confirm the token was issued *for it* — check the `aud` claim
equals this server's resource URI. *[SPEC; KB — "Model Context Protocol for
LLMs": "resource indicators bind a token to a specific server … an access token
issued for one MCP server cannot be redeemed against another."]* Without this, a
token minted for a different service is accepted here (privilege crossing). See
§7 for how the AS produces the binding.

### 4.3 Emit the discovery pointer on 401
Your `401` **MUST** carry `WWW-Authenticate: Bearer resource_metadata="<url>"`
pointing at your protected-resource metadata. *[SPEC — RFC 9728 §5.1]* This is
the single line that turns a dead 401 into the start of the OAuth flow.
*[DR — `mcp/auth.py` `_www_authenticate_challenge()`.]*

### 4.4 Transports
- **Streamable HTTP** is the current standard for remote MCP servers; SSE is the
  older form. *[KB — "Learn MCP with Python".]* This guide's OAuth flow applies
  to HTTP transports.
- **STDIO** servers do **not** use this flow — they read credentials from the
  environment. *[KB — "Agents All the Way Down"; [SPEC].]*

## 5. Building (or choosing) the Authorization Server

### 5.1 Dynamic Client Registration — RFC 7591
Expose `POST /register`: a client submits `redirect_uris` (+ metadata) and gets a
`client_id` (and secret only if confidential). *[KB — "OpenID Connect in
Action": RFC 7591 "provides a standard interface for client registration …
register a client application dynamically and obtain a client id and secret."]*
Claude Desktop relies on DCR so users don't hand-configure client IDs. *[SPEC.]*
Validate redirect URIs to **https or loopback** and store them for exact
matching. *[DR — `register_client`; open-DCR clients are public + PKCE.]*

### 5.2 Authorization endpoint + consent
`GET /authorize` authenticates the human (reuse your existing login), then shows
a **consent** screen naming the client and scope, and on approval issues a
short-lived, single-use authorization code bound to `(client_id, redirect_uri,
PKCE challenge, principal, resource)`. *[KB — "Clean Code Principles and
Patterns": the authorization-code redirect with `client_id`, `redirect_uri`,
`code`, `state`; [DR] `oauth_consent.html` + `issue_code`.]* Consent-per-client
is also your confused-deputy mitigation (§9).

### 5.3 The discovery documents
Serve both, as JSON, uncached *[SPEC]*:

- `GET /.well-known/oauth-protected-resource` (RFC 9728) → `{ resource,
  authorization_servers:[...], scopes_supported, bearer_methods_supported }`.
- `GET /.well-known/oauth-authorization-server` (RFC 8414) → `{ issuer,
  authorization_endpoint, token_endpoint, registration_endpoint,
  code_challenge_methods_supported:["S256"], grant_types_supported, ... }`.

*[DR — `oauth_web.py` serves both; note the corpus contains **no** coverage of
these documents, so this section is spec-only.]*

### 5.4 Token endpoint
`POST /token` supports:

- **`authorization_code`** — verify the code (single-use, unexpired, redirect +
  client match) and the **PKCE** `code_verifier` (§6), then mint access +
  refresh. *[KB — "Clean Code": `grant_type=authorization_code`,
  `code_verifier`; "Learn MCP with Python": "call /token to exchange the
  authorization code for an access token."]*
- **`refresh_token`** — rotate: consume the old refresh token, issue a new
  pair. For public clients, rotation is **required**. *[SPEC — OAuth 2.1 §4.3.1;
  KB — "OpenID Connect in Action" (grant types incl. refresh).]*
- **`client_credentials`** — for non-interactive service clients (§8). *[KB —
  "Model Context Protocol for LLMs" shows `grant_type=client_credentials`.]*

### 5.5 Self-host vs external IdP — how to choose
| | External IdP (Auth0/Keycloak/Entra) | Self-hosted AS |
|---|---|---|
| Build effort | RS only (validate JWT) | RS + AS (authorize/token/DCR/consent) |
| Login providers | the IdP's | yours (local/OIDC/SAML) |
| Token↔data binding | via claims you map | direct (bind to your authz model) |
| Best when | you already run an IdP | tokens must bind to row-level authz |

*[KB for the external pattern; DR for the self-hosted pattern.]*

## 6. PKCE — non-negotiable

*[SPEC — OAuth 2.1 §7.5.2; KB — "Clean Code".]* The client generates a random
`code_verifier`, sends `code_challenge = BASE64URL(SHA256(verifier))` with
`code_challenge_method=S256` at `/authorize`, and the raw `code_verifier` at
`/token`. The AS recomputes and compares. *[KB — "Clean Code": "code_challenge —
PKCE extension: URL-safe base64-encoded SHA256 hash of the code verifier …
code_challenge_method=S256 (SHA256)."]*

Rules: **require** PKCE, accept **only `S256`** (reject `plain`), and treat a
missing/mismatched verifier as `invalid_grant`. *[DR — `_verify_pkce`; tests
cover wrong-verifier, replay, and expiry.]*

## 7. Audience binding done right — RFC 8707

*[KB — "Model Context Protocol for LLMs" (verbatim): "MCP servers are now
formally defined as OAuth resource servers, and clients must include a resource
indicator (RFC 8707) when requesting access tokens … binding each token to a
specific server."]*

Mechanics:
1. The client sends `resource=<canonical MCP URI>` on **both** `/authorize` and
   `/token` (including refresh). *[SPEC.]*
2. The AS validates the resource (reject a foreign host), binds the token's
   `aud` to it, and this binding **survives refresh rotation**. *[DR —
   `_resolve_resource` + `aud=resource`; verified end-to-end: a token for one
   resource is rejected for another.]*
3. The RS validates `aud == its resource URI` (§4.2).

**Worked pitfall [DR]:** doc-ray originally minted tokens with a *shared* audience
and only host-validated `resource` — so tokens weren't cryptographically bound to
one server. The fix was to bind `aud` to the resolved resource everywhere and
carry it through rotation. Enforcement is a deployment detail: the RS's expected
audience must equal the resource URI, or the check is skipped.

## 8. `client_credentials` for service clients — safely

Non-interactive clients (no human) use `client_credentials` with a client secret.
*[KB.]* The hazard is specific to **open DCR**: anyone can register a client, so
if `client_credentials` minted a token for an arbitrary principal it would be
privilege escalation.

**Safe pattern [DR]:** gate the grant on an explicit **service-principal binding**
that only an operator can set. A confidential client an operator has bound to a
principal may use `client_credentials`; an anonymously-registered client has no
principal and is refused (`unauthorized_client`). Issue **no refresh token** for
this grant (OAuth 2.1). *[DR — `exchange_client_credentials`, `oauth_clients.
principal_id`; verified: refused unprovisioned, works once provisioned.]*

## 9. Hardening checklist

- [ ] PKCE **required**, `S256` only. *[SPEC/KB/DR]*
- [ ] `redirect_uri` matched **exactly** against registered values; https/loopback
      only. *[SPEC — "Open Redirection"; DR]*
- [ ] `state` used and verified (CSRF). *[SPEC; KB — "Clean Code": the flow
      "extends the Authorization Code Flow to prevent CSRF and authorization code
      injection attacks."]*
- [ ] Authorization codes **single-use**, short-lived (~60 s). *[SPEC; DR]*
- [ ] Access tokens **short-lived**; refresh tokens **rotated** for public
      clients. *[SPEC §7.1/§4.3.1; DR]*
- [ ] `aud` bound to the resource; RS validates it. *[SPEC 8707; DR — §7]*
- [ ] Consent shown **per client**, including per dynamically-registered client —
      this is the confused-deputy mitigation. *[SPEC — "Confused Deputy"; DR]*
- [ ] Least privilege: scope the grant; enforce authorization at the data layer
      (doc-ray uses Postgres RLS). *[KB — "MCP_Zero_To_Hero_2026" (least
      privilege); DR]*
- [ ] The RS returns `WWW-Authenticate` with `resource_metadata` on 401. *[SPEC
      9728; DR]*

## 10. Worked reference — doc-ray's implementation

*[DR — all verified.]* A concrete map from concept to code, useful as a template:

| Concept | Where |
|---|---|
| RS token validation + RLS binding | `mcp/auth.py` `BearerAuthMiddleware`; `verify_access_jwt` |
| Per-call token forwarding (survives mid-session refresh) | `mcp/tokens.py` `current_token()` — reads the live request, not a session-frozen contextvar |
| 401 discovery pointer | `mcp/auth.py` `_www_authenticate_challenge()` |
| PRM + AS metadata | `webapp/oauth_web.py` `oauth_protected_resource_metadata`, `oauth_authorization_server_metadata` |
| DCR | `oauth_web.py` `oauth_register` → `OAuthServerService.register_client` |
| Authorize + consent (PKCE) | `oauth_web.py` `oauth_authorize(_submit)`; `oauth_consent.html` |
| Token (code / refresh / client_credentials) | `oauth_web.py` `oauth_token`; `_issue_tokens(..., resource)` |
| PKCE / single-use codes / redirect match | `application/oauth_server.py` |
| Audience binding (RFC 8707) | `oauth_web.py` `_resolve_resource` + `aud=resource` |
| Service-client gate | `oauth_clients.principal_id` (migration `087`) |

## 11. Common mistakes (and where the corpus will mislead you)

1. **Stopping at bearer tokens.** The most common "MCP auth" is a shared secret —
   none of §1's threats are addressed.
2. **Skipping discovery.** The corpus never mentions PRM / `.well-known` /
   `WWW-Authenticate`, yet the client's flow *starts* there. Build them from the
   spec, not the books. *[gap]*
3. **`aud` not bound.** Host-validating `resource` without binding `aud` leaves
   tokens cross-usable. *[DR pitfall, §7]*
4. **Conflating OAuth 2.0 and 2.1.** The corpus's OAuth theory ("OpenID Connect
   in Action") teaches 2.0 grant types (incl. implicit); MCP mandates 2.1 (PKCE
   required, implicit removed). Don't copy a 2.0 flow. *[reconciliation]*
5. **Ignoring the confused deputy.** Not in the corpus at all; consent-per-client
   is the mitigation. *[SPEC gap]*
6. **`client_credentials` on open DCR.** Without a provisioning gate this is
   escalation. *[§8]*

## Appendix A — Specifications
OAuth 2.1 (draft), RFC 6749/9068 (OAuth/JWT profile), RFC 7591 (DCR), RFC 7636
(PKCE), RFC 8414 (AS metadata), RFC 8707 (resource indicators), RFC 9728
(protected-resource metadata), MCP Authorization spec (2025-06).

## Appendix B — Corpus source map
| Topic | Best corpus source |
|---|---|
| OAuth/OIDC theory, grant types, DCR, SSO | *OpenID Connect in Action (MEAP V13)* |
| Hands-on OAuth: PKCE, auth-code, token endpoint | *Clean Code Principles & Patterns (2nd ed)* |
| API gateway / JWT / access control | *Securing Microservice APIs* |
| Modern MCP auth (resource server, RFC 8707) | *Model Context Protocol for LLMs* |
| Practical MCP + OAuth2 implementation | *Learn Model Context Protocol with Python* |
| MCP concepts + risks | *MCP: Advanced AI Agents for Beginners* |
| Agent authz, prompt injection, A2A vs MCP | *Principles of Building AI Agents* |

## Appendix C — Glossary
**AS/RS** authorization/resource server · **DCR** dynamic client registration ·
**PKCE** proof key for code exchange · **resource indicator** RFC 8707 audience
binding · **PRM** protected-resource metadata (RFC 9728) · **confused deputy** an
intermediary tricked into using its own authority for an attacker.

---

> Coverage note: §§4.3, 5.3, and the confused-deputy material are **[SPEC]-only**
> — the doc-ray corpus does not cover the discovery documents or the confused
> deputy. Treat those as the guide's external dependencies, and re-check them
> against the current MCP authorization spec before relying on them.
