---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-15"
doc_type: readme
---

# doc-ray · Admin Console

A React (TypeScript + Vite) backoffice for operating the doc-ray SaaS, packaged
as its **own compose project** (`docray-admin`) so it deploys and scales
independently of the main stack. It talks to the existing doc-ray API and Flask
webapp over the **same origin** through an nginx reverse proxy (the API ships no
CORS, so same-origin proxying is required — not a preference).

Up one level: [../README.md](../README.md).

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Scope — what is wired, and what is an honest gap

This is a **frontend shell** built against the API contract that exists today
(verified against the codebase on 2026-07-15). It does not fabricate endpoints.

| Area | Status | Backing endpoint |
|---|---|---|
| Operator sign-in (JWT) | ✅ wired | `POST /auth/login`, `/auth/refresh` |
| Operational dashboard | ✅ wired | `GET /health`, `/readyz`, `/operator/health.json` |
| Jobs: monitor + cancel + re-extract | ✅ wired | GraphQL `jobs`, `cancelJob`, `reextractDocuments` (admin) |
| Documents overview | ✅ wired | GraphQL `documents` |
| Security audit log | ✅ wired | GraphQL `auditEvents` (admin) |
| Backups: create / restore | ✅ wired, feature-gated | GraphQL `createBackup`, `restoreBackup` (admin). Hidden from the nav and refused by the API unless `DOCRAY_BACKUP_ENABLED=true` (D-033); the console reads `backupEnabled` to decide. Off by default. |
| **Tenants / users / roles / ACLs** | ⚠️ **no HTTP API yet** | CLI-only (`auth_admin.py`) + SCIM/OIDC/SAML |

The "Tenants & users" screen is deliberately honest: tenant/principal/role/ACL
administration has **no GraphQL or REST endpoint** in doc-ray today — it is
CLI-driven. Rather than wire buttons to routes that would 404 (which would
violate the project's no-placeholder / PALS's-Law rules), that screen:

1. builds the **exact CLI command** an operator runs today (live preview), and
2. is coded against a typed `IdentityProvider` interface whose default
   implementation rejects with a documented `IdentityApiUnavailableError`.

**To make identity management fully interactive**, add backend GraphQL mutations
(tenant CRUD, `createPrincipal`, `assignRole`, `grantDocumentAcl`) plus their
RLS wiring, then implement an `HttpIdentityProvider` satisfying the existing
[`IdentityProvider`](src/api/identity.ts) interface and swap the binding in
[`services.tsx`](src/services.tsx). No UI rewrite is required.

## Prerequisites (on the doc-ray API)

Sign-in uses local password auth, which is **off by default**. On the `api` (and
`webapp`) services set:

- `DOCRAY_AUTH_LOCAL_ENABLED=true` and a `DOCRAY_API_JWT_SECRET` (JWTs are
  otherwise unconfigured and `/auth/login` returns 503).
- An **admin** principal with local credentials (see
  `src/doc_ray/entrypoints/auth_admin.py`: `principal create --role admin` then
  `db-login create` / local credential setup). Admin-gated screens (audit,
  cancel-job, backups) require `docray_is_admin()` to hold for the caller.
- `DOCRAY_OPERATOR_HEALTH_TOKEN` on the webapp for the operator-health snapshot.
  Without it — or without a bootstrapped operator session on this origin (open
  `/operator/?token=…` once) — `/operator/health.json` returns **404** and the
  dashboard shows an authorization notice that names the token. The panel now
  reports other failures *distinctly*, never relabeling them as a token problem
  (issue #22): a **JSON / content-type error** means `/operator/*` is not being
  reverse-proxied to the webapp — nginx served the SPA shell instead, so check
  the `location /operator/` block and that `WEBAPP_UPSTREAM` resolved — and a
  5xx is a webapp fault. The snapshot itself follows the `operator-health.v1`
  contract (`webapp/health_contract.py`): corpus metrics under `corpus.totals`,
  the job spool under `corpus.jobs`, and issues (with a `detail` field) under
  both `corpus.issues` and `database.issues`.

## Run it

### With Docker (recommended)

The console attaches to the main stack's network to reach `api`/`webapp` by name.

```bash
# 1. Bring up the main doc-ray stack first (creates network doc-ray_default).
docker compose up -d

# 2. Build + start the console (separate project: docray-admin).
docker compose -f admin-console/docker-compose.yml up -d --build

# 3. Open the console (bound to loopback like the webapp/postgres).
open http://127.0.0.1:8080
```

For the managed production stack, configure the admin-console values in
`.env.prod` and use the root operator targets:

```bash
make prod-admin-console-up
make prod-admin-console-down
```

If your main compose project name is not `doc-ray`, set `DOCRAY_NETWORK` to its
`<project>_default` network. The service also joins that network with the stable
`admin-console` alias, so a Cloudflare Tunnel Public Hostname can target
`http://admin-console:80`. See [.env.example](.env.example) for all overrides.

For live Stripe billing, open the console through the same trusted HTTPS origin
configured in `DOCRAY_STRIPE_SUCCESS_URL`, `DOCRAY_STRIPE_CANCEL_URL`, and
`DOCRAY_STRIPE_PORTAL_RETURN_URL`. The Billing page sends its current `/billing`
URL as the hosted Checkout/Portal return target; the API rejects cross-origin
overrides when billing enforcement is enabled. For the Cloudflare Tunnel
production path, publish `admin.doc-ray.com` to `http://admin-console:80` and
protect it with Cloudflare Access before sending operators there.
Hosted Stripe Checkout and Customer Portal remain disabled until
`DOCRAY_STRIPE_WEBHOOK_SECRET` and `DOCRAY_PUBLIC_BASE_URL` configure signed
webhook processing for `/stripe/webhook`; doc-ray updates paid access from
Stripe webhook events, not from the browser return alone.

### Local dev

```bash
cd admin-console
npm install
npm run dev          # http://localhost:5173, proxying to localhost:8000 / :5001
```

The dev server proxies the same routes as the production nginx, so behavior
matches a running doc-ray stack.

## Tests

Test-driven; the data layer, auth flow, token store, and the identity-gap
contract are all covered, plus React Testing Library specs for the auth guard
and login.

```bash
cd admin-console
npm test             # vitest run
npm run typecheck    # tsc --noEmit
npm run build        # typecheck + production bundle
```

No local Node? Run the suite in a container:

```bash
docker run --rm -v "$PWD":/app -w /app node:20-alpine sh -c "npm ci && npm test"
```

## Architecture

```
Browser ──> nginx (this container, :80)
              ├─ /            → static React SPA (dist/)
              ├─ /graphql     → API  (GraphQL: reads + admin mutations)
              ├─ /auth/*      → API  (JWT login / refresh)
              ├─ /health,/readyz,/metrics → API
              └─ /operator/*  → Flask webapp (operator health snapshot)
```

- [src/api/](src/api/) — typed transport: `client.ts` (GraphQL+REST, bearer
  injection, single-shot refresh-and-retry on 401) and per-domain modules that
  mirror the backend contract verbatim.
- [src/auth/](src/auth/) — `TokenStore` (persistence + expiry/skew), `AuthService`
  (login/refresh/logout), and the React `AuthContext`.
- [src/pages/](src/pages/) — one page per operational area.
- Dependencies are injected through [src/services.tsx](src/services.tsx) so tests
  run with fakes and production wires the live, same-origin client.

## Security notes

- Published on `127.0.0.1` by design (mirrors the webapp/postgres pattern). Front
  it with TLS and network controls before exposing it beyond a trusted host.
- The console holds JWTs in `localStorage`; access tokens are short-lived and
  refresh rotates. Admin authority is enforced **server-side** by Postgres RLS
  and `docray_is_admin()` — the UI only reflects it.
- nginx sends `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and a
  `no-referrer` policy; the page is marked `noindex`.
