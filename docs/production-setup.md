---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-15"
doc_type: runbook
---

# Production Setup — Managed Postgres + Spaces + Cloudflare Tunnel

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

This runbook stands up a production doc-ray on **managed** DigitalOcean
services — a **Managed PostgreSQL** cluster, **Spaces** for the content-addressed
blob store, and a **Droplet** running only the stateless app services — reached
through a **Cloudflare Tunnel** so the origin publishes **no public web ports**.
For the self-hosted alternative (Postgres + MinIO as containers on the Droplet),
run the base [`docker-compose.yml`](../docker-compose.yml) directly; use *this*
guide when you want the data and object storage managed.

```
                       Internet
                          │  HTTPS — TLS terminates at the Cloudflare edge
                          ▼
                     Cloudflare  ── doc-ray.com (proxied DNS + Tunnel ingress)
                          │  outbound-only tunnel (QUIC); NO origin ports open
                          ▼
        ┌──────────────────────────────────────────┐
        │  Droplet (all app ports bind 127.0.0.1)   │
        │   cloudflared ─► webapp (gunicorn)        │
        │   api (uvicorn) · worker                   │
        └──────────┬────────────────────┬───────────┘
        DATABASE_URL (TLS)         S3_ENDPOINT (TLS)
                   ▼                     ▼
        ┌────────────────────────┐  ┌───────────────────────┐
        │ DO Managed PostgreSQL  │  │ DO Spaces (S3)        │
        │ pgvector · TLS · PITR  │  │ content-addressed blobs│
        └────────────────────────┘  └───────────────────────┘
```

The whole flow is driven by `make` targets that load a dedicated **`.env.prod`**
and layer the production overlay — never the development `.env`.

---

## ⚠ Sizing — read this first

doc-ray's database is **disk- and RAM-bound**. Measured on the current corpus:

| Signal | Value |
| --- | --- |
| DB size today | **~32 GB** at **143 of 268** documents fully annotated |
| Per fully-annotated + enriched document | ~215 MB |
| Steady state once the current corpus finishes annotating | **~60 GB** |

**A 60 GiB disk is the floor, not a safe size.** Postgres needs headroom above
the raw DB size for WAL, table/index bloat, HNSW/GIN rebuilds, and backup
activity. **Provision ~120 GiB** (rule of thumb: 1.5–2× steady-state DB size).
DigitalOcean lets you **increase disk in place** at $0.21/GiB/mo with no rebuild,
so scale it up **before** loading and annotating the full corpus — running out of
disk on a managed cluster wedges writes.

The **8 GB RAM / 2 vCPU** compute is well matched: DO sets `shared_buffers`≈2 GB,
which holds the pgvector HNSW index plus the hot B-trees resident. A
high-availability standby node doubles the monthly cost — add it only if an
uptime SLA requires it (DO's automated backups + PITR already protect the data).

**Postgres version.** doc-ray is built and tested on **PostgreSQL 16** (the
compose image pins `pgvector/pgvector:pg16` at an immutable digest, and the
migrations/docs target 16). A newer major runs — the reference deployment is on
**PostgreSQL 18** — but it is **not in the tested matrix**; treat it as
unvalidated and watch the migration + health output on first bring-up.

---

## Prerequisites

- **Managed PostgreSQL cluster** created (`sslmode=require`, region **nyc3**).
  Have ready: `host`, direct `port` (`25060`), `defaultdb`, admin `doadmin`, and
  the admin password. Prefer PostgreSQL 16 (see the sizing note above).
- **A Droplet** with Docker installed, the firewall limited to SSH, and this repo
  checked out. Do **not** start the `postgres`/`minio` containers on it (the prod
  overlay gates them out).
- **A Cloudflare-managed domain** (e.g. `doc-ray.com`) for the Tunnel ingress.
- `psql` for one-time cluster setup — or run it from a throwaway container:
  `docker run --rm -e PGPASSWORD -it postgres:16 psql …` (no host install needed).

Everything below assumes you are in the checkout on the Droplet.

---

## Step 1 — Prepare the Managed Postgres cluster

Create a **dedicated database and least-privilege role** (never run the app as
`doadmin`), then enable pgvector. `docray` should **not** have `CREATEDB`, so the
database is created by `doadmin`.

```bash
ADMIN="postgresql://doadmin:<ADMIN_PW>@<CLUSTER_HOST>:25060/defaultdb?sslmode=require"

psql "$ADMIN" <<'SQL'
CREATE ROLE docray LOGIN PASSWORD '<STRONG_APP_PASSWORD>';
CREATE DATABASE docray OWNER docray;
SQL

# pgvector must be enabled INSIDE the docray database (doc-ray requires it):
psql "postgresql://doadmin:<ADMIN_PW>@<CLUSTER_HOST>:25060/docray?sslmode=require" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

> DO Managed PostgreSQL ships the `vector` extension at no extra cost. If
> `CREATE EXTENSION vector` errors, enable it from **Settings → Extensions** first.

> ⚠ **`docray` must NOT be a superuser.** RLS is forced on every corpus table
> (migration 106, schema 1.36.0), and PostgreSQL exempts a superuser from RLS no
> matter what — so an app connecting as a superuser would silently bypass the
> isolation the whole model depends on. The role above is a plain `LOGIN` role,
> which is correct; do not grant it `SUPERUSER`.

**Grant the batch-writer role its RLS exemption (one superuser statement).**
Migration 106 creates a `docray_batch` role for the worker/CLIs and needs it to
carry `BYPASSRLS`, which only a superuser can set. The migration runs as `docray`
(not a superuser), so it **raises and stops** until you run this once as
`doadmin` — before or after the first migrate; re-running migrate then succeeds:

```bash
psql "postgresql://doadmin:<ADMIN_PW>@<CLUSTER_HOST>:25060/docray?sslmode=require" \
  -c "ALTER ROLE docray_batch BYPASSRLS;"
```

If `docray_batch` does not exist yet (first migrate hasn't created it), run one
migrate first — it will create the role, raise on the missing `BYPASSRLS`, then
this statement plus a second migrate completes the schema.

**Trusted sources (firewall).** In **Settings → Trusted Sources**, restrict
inbound to the Droplet (or its VPC). Prefer the **VPC endpoint** when the Droplet
shares the cluster's VPC — traffic stays private. If the Droplet is in a
different VPC, use the public endpoint and add its IP (note: an **ephemeral GPU
box's public IP changes per respawn**, so place GPU droplets in the DB's VPC).

**Connection pooling — use the direct endpoint.** doc-ray pools client-side
(`psycopg_pool`, `DOCRAY_API_POOL_MAX_SIZE`); its backend footprint is small
(API pool + workers ≪ the ~97 backends an 8 GB cluster allows). Point
`DATABASE_URL` at the **direct** port `25060`. Only add DO's PgBouncer pool under
real connection pressure, and then in **session** mode (`psycopg3` auto-prepares
statements; transaction-mode pooling breaks them).

## Step 2 — Provision Spaces as the blob store

1. Create a **dedicated bucket**, e.g. `doc-ray-prd`, **File Listing: Restricted**.
2. Create a **bucket-scoped access key** (Applications & API → Spaces Keys),
   Read/Write, scoped to that bucket. Copy the secret once.
3. Use the **regional endpoint** — `https://nyc3.digitaloceanspaces.com` — with
   the bucket as a **separate** `S3_BUCKET` value. Do **not** use the
   bucket-in-hostname form (`doc-ray-prd.nyc3.digitaloceanspaces.com`) as the
   endpoint; that double-prefixes the bucket. A bucket-scoped key cannot list
   *all* buckets — that "Access Denied" on a global `ls` is expected and correct.

## Step 3 — Production environment file (`.env.prod`)

Production has its **own** env file, never the development `.env`. Copy the
template and fill it in:

```bash
cp .env.prod.example .env.prod
chmod 600 .env.prod
$EDITOR .env.prod
```

The `make prod-*` targets load it with `docker compose --env-file .env.prod …`,
which **replaces** `.env` for interpolation — so `.env.prod` holds only the
production-specific values; everything else inherits its base-compose default.
Keep development values in `.env`; never mix the two (a regression test enforces
that prod uses `.env.prod`).

Minimum production values (`.env.prod.example` documents the full set):

```bash
# Managed Postgres (direct endpoint, TLS)
DATABASE_URL=postgresql://docray:<STRONG_APP_PASSWORD>@<CLUSTER_HOST>:25060/docray?sslmode=require
# The base compose interpolates ${POSTGRES_PASSWORD} even though the local
# postgres container is gated out in prod — give it any non-default value.
POSTGRES_PASSWORD=<any-non-default-value>

# Spaces
S3_ENDPOINT=https://nyc3.digitaloceanspaces.com
S3_BUCKET=doc-ray-prd
S3_ACCESS_KEY=<SPACES_KEY>
S3_SECRET_KEY=<SPACES_SECRET>
S3_REGION=nyc3
S3_ALLOW_INSECURE_ENDPOINT=0

# API — fail closed
DOCRAY_API_ALLOW_ANONYMOUS=false
DOCRAY_API_DEBUG=
DOCRAY_API_JWT_SECRET=<openssl rand -hex 32>

# Browser auth — require login (no open corpus)
DOCRAY_AUTH_LOCAL_ENABLED=true
DOCRAY_AUTH_REGISTRATION_ENABLED=true
DOCRAY_WEBAPP_REQUIRE_AUTH=true
DOCRAY_WEBAPP_SECRET_KEY=<openssl rand -hex 32>   # REQUIRED: signs session/CSRF cookies
DOCRAY_WEBAPP_SESSION_COOKIE_SECURE=true
DOCRAY_PUBLIC_BASE_URL=https://doc-ray.com        # verification-email links

# SMTP — required for registration (else /auth/register fail-closes 503).
# The mailer speaks STARTTLS only (no implicit-TLS/465). Example below: SMTP2GO.
DOCRAY_SMTP_HOST=mail.smtp2go.com     # region variants: mail-us / mail-eu / mail-au .smtp2go.com
DOCRAY_SMTP_PORT=2525                 # STARTTLS. DO droplets block 25/465/587 — use 2525/8025.
DOCRAY_SMTP_STARTTLS=true
DOCRAY_SMTP_FROM=noreply@doc-ray.com  # must be a sender/domain the relay has verified
DOCRAY_SMTP_USERNAME=<relay SMTP user>      # SMTP2GO: Sending → SMTP Users (not your account login)
DOCRAY_SMTP_PASSWORD=<relay SMTP password>

# Cloudflare Tunnel (Step 6)
CLOUDFLARE_TUNNEL_TOKEN=

# Worker pool
DOCRAY_WORKERS=3
```

Notes:
- `DOCRAY_WEBAPP_SECRET_KEY` is **mandatory** in production: it signs the session
  and CSRF cookies. Unset, gunicorn generates a random key at startup, so every
  restart silently invalidates all logins.
- `S3_ENDPOINT` is `https://`, so `S3_ALLOW_INSECURE_ENDPOINT=0` (that flag is
  only for local plaintext MinIO).
- **Registration email (SMTP).** Self-service registration fail-closes **503**
  until **both** `DOCRAY_SMTP_HOST` and `DOCRAY_PUBLIC_BASE_URL` are set — the raw
  verification token is discarded after the request, so with no delivery path the
  address would be stranded (the `/auth/register` handler in
  [`auth_routes.py`](../src/doc_ray/entrypoints/api/auth_routes.py)).
  The stdlib adapter
  ([`mailer_smtp.py`](../src/doc_ray/adapters/mailer_smtp.py)) speaks **STARTTLS
  only** — it has no implicit-TLS path, so use a STARTTLS port (587, or 2525/8025
  if 587 egress is blocked), **never** 465/8465. `DOCRAY_SMTP_USERNAME` +
  `DOCRAY_SMTP_PASSWORD` together enable AUTH; `DOCRAY_SMTP_FROM` must be a sender
  the relay has verified, or the message is rejected and registration returns
  **202** with `verification_email_sent:false` (logged as `verification email
  delivery failed`). Confirm the relay before trusting the flow — this surfaces
  the real error instead of the swallowed `false`:
  ```bash
  make prod-check TO=you@example.com    # full check, ending in a real delivery
  make prod-check NO_SEND=1             # connect + STARTTLS + AUTH only
  ```
- **Port 587 is blocked on DigitalOcean droplets.** DO blocks outbound SMTP
  (25/465/587) by default, so a correct SMTP2GO configuration still fails with a
  bare `timed out` — the failure looks like bad credentials but is egress. Verify
  before debugging the relay:
  ```bash
  for p in 587 2525 8025; do timeout 5 bash -c "exec 3<>/dev/tcp/mail.smtp2go.com/$p" \
    && echo "$p open" || echo "$p blocked"; done
  ```
  SMTP2GO serves STARTTLS on **2525** and **8025**, which are not blocked — set
  `DOCRAY_SMTP_PORT=2525`. Changing it requires recreating the containers
  (`make prod-refresh-ui`), since env is read at process start.

## Step 4 — Apply the schema

The prod overlay [`docker-compose.prod.yml`](../docker-compose.prod.yml) repoints
every app service at the managed cluster + Spaces and **gates the local
Postgres/MinIO out** (parked on a never-enabled `local-infra` profile), so a bare
`up` can never start a stray local database.

```bash
make prod-migrate         # via the migrate container
make prod-migrate-host    # AUTHORITATIVE: applies from the repo working tree
```

> ⚠ **Verify migrations actually applied — do not trust "0 applied" alone.**
> `make prod-migrate` (the container path) has been observed to report
> **`0 applied` while migrations were still pending**, so code ships without its
> schema and features break at runtime (e.g. OAuth/DCR 500 on a missing
> `oauth_clients` table). Always finish with **`make prod-migrate-host`** (runs the
> migrator from the repo working tree) and confirm the contract version matches the
> code before declaring the deploy done:
> ```bash
> # both must equal src/doc_ray/__init__.py __version__
> psql "$DATABASE_URL" -c "SELECT docray_schema_version();"
> ```
> A schema version behind the code = migrations did **not** apply, regardless of
> what the container path printed.

> **1.53.1 sentence-denominator repair (#42).** Migration 146 deliberately runs a
> full read-model refresh: it backfills `corpus_stats.n_sentences` from maintained
> `document_stats`, repairs the deploy-time path migration 142 missed, and aborts if
> either corpus refresh helper is not a `SECURITY DEFINER` function owned by the
> `BYPASSRLS` `docray_batch` role. After migrations 146–147, run
> `make prod-check NO_SEND=1` and require the `corpus_rollups` check to pass before
> declaring the deploy healthy. The check reads only maintained rollups; it does not
> count live sentence or lexeme facts. No environment/configuration change is
> required.

Then load the corpus by whichever fits:
- **Fresh ingest from Spaces** — idempotent, content-addressed
  (`scripts/ingest_from_spaces.py`). Each new document also enqueues the standard
  post-ingest pipeline, so the worker must be running for the load to finish;
  add `--owner <login-email>` unless the corpus is meant to stay admin-only.
  Objects that fail extraction (typically scanned PDFs with no text layer) land
  as `status='failed'` documents and are *not* retried on later runs: identical
  bytes dedupe to the same failed row before extraction can re-run. OCR the file
  — which changes its ETag — and it re-enters on the next pass.
- **Restore a snapshot** — `createBackup` on the old DB, `restoreBackup` on prod
  (schema versions must match). Requires `DOCRAY_BACKUP_ENABLED=true` on *both*
  ends; the subsystem is off by default (D-033).
- **`pg_dump`/`pg_restore`** — or DO's *"Migrate from an existing PostgreSQL
  database"* flow for a one-shot cutover.

## Step 5 — Start the production stack

```bash
make prod-up          # migrate, then: api + webapp + worker + worker-fast
```

Production web tier (the base compose's dev servers are overridden in the prod
overlay):
- **webapp → gunicorn** — `flask run`'s Werkzeug dev server is never used in
  production. It runs a **single** gunicorn worker with 8 gthread threads:
  concurrency comes from threads (which share the in-process login-session store),
  not workers. Do **not** raise `DOCRAY_WEBAPP_WORKERS` above 1 without first
  swapping in a shared (DB-backed) session store — a login handled by one worker
  is invisible to the others, so requests intermittently fail as "authentication
  required".
- **api → uvicorn**, `DOCRAY_API_WORKERS` (default 2) — the API is stateless, so
  multiple workers are safe.
- Both bind **`127.0.0.1`** only — they are reached over the compose network
  (webapp → `api:8000`) and externally only through the tunnel (Step 6). Raw
  GraphQL is never published on a public port.

The React admin console, including the Stripe Billing panel, runs as a separate
compose project so it can be rebuilt or stopped without touching workers/API:

```bash
make prod-admin-console-up
```

It attaches to `DOCRAY_NETWORK` (default `doc-ray_default`), publishes the
in-network alias `admin-console`, and binds
`127.0.0.1:${ADMIN_CONSOLE_PORT:-8080}:80`. Keep the host bind loopback-only.
For a browser-reachable production Billing panel, route a protected Cloudflare
Tunnel hostname to `http://admin-console:80` and enforce Cloudflare Access (or an
equivalent trusted identity boundary) on that hostname. Stop it with
`make prod-admin-console-down`.

For live Stripe Checkout and Customer Portal flows, the Billing panel passes its
current `/billing` URL as the hosted-session success/cancel/return target. Set
`DOCRAY_STRIPE_SUCCESS_URL`, `DOCRAY_STRIPE_CANCEL_URL`, and
`DOCRAY_STRIPE_PORTAL_RETURN_URL` to that same trusted HTTPS admin-console
origin, for example `https://admin.doc-ray.com/billing`, while keeping
`DOCRAY_PUBLIC_BASE_URL` on the public `doc-ray.com` origin for
`/stripe/webhook`.

Create the Stripe webhook endpoint for `https://doc-ray.com/stripe/webhook`
and select the event types the Billing panel lists as required:
`checkout.session.completed`, `checkout.session.expired`,
`customer.subscription.created`, `customer.subscription.updated`,
`customer.subscription.deleted`, `invoice.paid`,
`invoice.payment_succeeded`, `invoice.payment_failed`, and
`invoice.finalization_failed`. Put the endpoint signing secret in
`.env.prod` as `DOCRAY_STRIPE_WEBHOOK_SECRET`. Hosted Stripe Checkout and
Customer Portal flows are blocked until this signed webhook processing path is
configured, because doc-ray grants tenant access from webhook-derived
subscription state rather than from the browser return alone.

## Step 6 — Cloudflare Tunnel ingress (public access)

The tunnel dials **outbound** to Cloudflare, so `doc-ray.com` is served with
**zero public ports** on the Droplet (SSH stays the only public surface). One-time
setup in the **Cloudflare Zero Trust dashboard** (Networks → Tunnels):

1. **Create a tunnel** (connector: Cloudflared), e.g. `doc-ray-prod`. Copy the
   **token** — the `eyJ…` string only, not the whole install command.
2. Put it in `.env.prod`: `CLOUDFLARE_TUNNEL_TOKEN=eyJ…`.
3. **Public Hostname → Add:** Domain `doc-ray.com`, Service **HTTP** →
   **`webapp:5001`** (the internal service name, not localhost). This also
   **auto-creates the proxied DNS record** — routing does not work until this
   step is done.
4. **Protected admin hostname → Add:** Domain `admin.doc-ray.com`, Service
   **HTTP** → **`admin-console:80`**. Protect this hostname with **Cloudflare
   Access** before using it for the Stripe Billing panel; it is the trusted HTTPS
   origin used by `DOCRAY_STRIPE_SUCCESS_URL`, `DOCRAY_STRIPE_CANCEL_URL`, and
   `DOCRAY_STRIPE_PORTAL_RETURN_URL`.

```bash
make prod-tunnel      # docker compose --env-file .env.prod ... -f docker-compose.cloudflared.yml up -d cloudflared
docker logs docray-cloudflared | grep -i "Registered tunnel connection"   # expect 4 connections
```

> **Not using Cloudflare?** `make prod-web` starts the bundled nginx TLS front
> door (`0.0.0.0:80/443`) instead — supply real certificates in the `nginxcerts`
> volume (nginx-certgen only issues a self-signed cert for bootstrap).

## Step 7 — Seed the admin, then verify

Create the admin token from the seeded `bootstrap-admin` principal and wire the
webapp's write path to it:

```bash
DATABASE_URL="$(grep '^DATABASE_URL=' .env.prod | cut -d= -f2-)"
ADMIN_ID="$(psql "$DATABASE_URL" -Atc \
  "SELECT id FROM principals WHERE display_name='bootstrap-admin';")"
API_TOKEN="$(openssl rand -hex 32)"
# add to .env.prod:
#   DOCRAY_API_TOKENS=$API_TOKEN=$ADMIN_ID:read+write
#   DOCRAY_WEBAPP_API_TOKEN=$API_TOKEN
make prod-up          # re-apply with the tokens set
printf 'Store this API token in your password manager: %s\n' "$API_TOKEN"
```

Verify end-to-end — one command covers config, the managed cluster and its
migration lockstep, Spaces, the API's readiness contract, the webapp, the MCP
surface (loopback and public `/mcp`), the Cloudflare Tunnel, the public URL, and
a **real** message through the SMTP relay:

```bash
make prod-check                          # sends the mail test to DOCRAY_SMTP_CHECK_TO
make prod-check TO=you@example.com       # …or an explicit recipient
make prod-check NO_SEND=1                # relay reachability + AUTH only, nothing delivered
```

Exit codes: `0` all green, `1` warnings only, `2` at least one failed check. The
SMTP probe is the only one that writes anything anywhere, and it is the only
check that exercises outbound mail at all — a broken relay is otherwise invisible
until a real user's registration returns 503.

`corpus_rollups` is a release-blocking database-integrity probe. It compares
tenant-level `corpus_stats` with the maintained `document_stats` and `lexeme_stats`
inputs while assuming `docray_batch`; a failure means a denominator or vocabulary
rollup is stale even if the API itself is reachable.

The individual probes, if you want them by hand:

```bash
curl -fsS http://127.0.0.1:8000/readyz            # "ready":true (DB + Spaces reachable)
psql "$DATABASE_URL" -c "SELECT docray_schema_version();"
# instance health against the managed cluster (override the uvicorn entrypoint):
docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm --entrypoint python api -m doc_ray.entrypoints.db_health
curl -I https://doc-ray.com                       # HTTP/2 200 — public landing page (served via Cloudflare)
curl -I https://doc-ray.com/documents             # HTTP/2 302 -> /login (auth wall on corpus routes)
```

`REQUIRE_AUTH=true` gates the **corpus**, not the front door: `/` serves a public
landing page (**200**, with Login/Register links), while protected routes
(`/documents`, `/search`, `/upload`, `/profile`, …) redirect unauthenticated
visitors to `/login` (**302**). Until an admin exists (above) or self-service
registration + SMTP is configured, `/login` is the wall — this is the intended
fail-closed posture. (Verify against a protected route: a `200` at `/` only means
the landing page renders, not that auth is enforced.)

---

## MCP tokens — mint, rotate, revoke (Ops Phase 3, #18)

The MCP server at `/mcp` authenticates every call with the **same** credentials as
the GraphQL API: either a static `DOCRAY_API_TOKENS` entry, or an OAuth 2.1 access
JWT obtained via Dynamic Client Registration (DCR). Two paths, chosen by client:

- **Static token** — for a scripted or headless agent you control. You mint it and
  hand it over out-of-band. Best for CI, cron, and your own tools.
- **OAuth 2.1 DCR** — for an interactive connector (e.g. Claude Desktop). The client
  registers itself and completes `/authorize` → `/token` (PKCE) with **no pre-shared
  secret**; the resulting access JWT is validated by the same middleware. Use this
  when a human signs in through a connector; you mint nothing.

> Every command below was executed against a running stack; the tokens shown are
> obviously fake. Never paste a real token into a doc, a ticket, or a commit.

### Mint a read-scoped token for a new agent

`read` is **not** the CLI default (`--scopes` defaults to `read+write`), so pass it
explicitly — an agent that only retrieves needs nothing more:

```bash
set -a; source .env.prod; set +a     # loads DATABASE_URL (real host + password)
# 1. create a service principal for the agent (once), then mint its token:
python -m doc_ray.entrypoints.auth_admin principal create \
  --display-name "agent-nightly-summariser" --kind service --role reader
python -m doc_ray.entrypoints.auth_admin token mint \
  --principal "agent-nightly-summariser" --scopes read
# → DOCRAY_API_TOKENS=<generated-token>=<principal-uuid>:read
```

Add that assignment to `.env.prod`'s `DOCRAY_API_TOKENS` (comma-separate multiple
entries), then `make prod-up` to re-apply. The MCP service reads the same variable,
so the token works at `/mcp` verbatim — point the agent at
`https://doc-ray.com/mcp` with `Authorization: Bearer <generated-token>`.

`--env-file .env.prod` will append the entry for you (and `--fail-closed` also sets
`DOCRAY_API_ALLOW_ANONYMOUS=false`); it prints `restart_required=true` as a reminder
to `make prod-up`. There is also a `mcp-token mint` subcommand with identical flags,
for when you want the mapping labelled as MCP-specific.

### Why `write` is not the default for agents

A `read+write` token can reprocess, tag, and mutate collections through the MCP
write tools (only when `DOCRAY_MCP_ENABLE_WRITE=true`, and every call is still
gated by the API's `write` scope + per-document ACLs). Grant it only to an agent
that genuinely curates; a retrieval agent with `write` is unnecessary blast radius.
Mint write only deliberately: `--scopes read+write`.

### Rotate

Minting is additive, so rotation is: mint the replacement, update the client, then
remove the old entry.

```bash
python -m doc_ray.entrypoints.auth_admin token mint --principal "<same-principal>" --scopes read
# update the agent's stored credential to the new token, confirm it works, THEN
# delete the old `=<uuid>:read` entry from DOCRAY_API_TOKENS in .env.prod
make prod-up
```

### Revoke

Remove the token's entry from `DOCRAY_API_TOKENS` in `.env.prod` and `make prod-up`.
The middleware only trusts tokens present in that map (or valid JWTs), so a removed
static token is rejected on the next request with `401`. To revoke an OAuth client,
delete its registration (`oauth_clients` table) so its refresh path stops issuing new
access JWTs; in-flight JWTs expire on their own short TTL.

### Read the audit trail for a principal

Every MCP tool call emits one line on the `doc_ray.mcp.audit` logger carrying a
**non-reversible fingerprint** of the caller's token (never the token itself),
the tool, the outcome, and the duration — and rate-limit rejections likewise on
`doc_ray.mcp.ratelimit`. Grep the `mcp` container logs by fingerprint to see one
agent's activity:

```bash
# the fingerprint is sha256(token)[:12]:
FP="$(python -c 'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:12])' "<the-token>")"
docker logs docray-mcp 2>&1 | grep "caller=$FP"
```

---

## Operations

**Make targets** (each loads `.env.prod`):

| Target | Action |
| --- | --- |
| `make prod-check` | Verify the live deployment: config, DB + migration lockstep, Spaces, API `/readyz`, webapp, MCP (local + public `/mcp`), Cloudflare Tunnel, public URL, and a real SMTP delivery (`TO=`, `NO_SEND=1`, `JSON=1`) |
| `make prod-migrate` | Apply migrations to the managed cluster |
| `make prod-up` | Migrate, then start api + webapp + workers |
| `make prod-admin-console-up` | Build/start the loopback-only React admin console and Stripe Billing panel |
| `make prod-admin-console-down` | Stop the separate admin console stack |
| `make prod-tunnel` | Start the Cloudflare Tunnel ingress (no public ports) |
| `make prod-web` | Start the nginx TLS front door instead (public 80/443) |
| `make prod-down` | Stop the managed stack (managed data is off-box; nothing wiped) |
| `make gpu-up` | Start `DOCRAY_WORKERS` GPU annotate workers (adds the CDI overlay) |

- **Disk headroom.** Watch `db_health` (or DO's dashboard). The 32 GB → ~60 GB
  annotation growth means **resize disk to ~120 GiB before the corpus finishes
  annotating.** Resizes are in place — do it early.
- **Backups.** DO's automated daily backups + PITR are the recovery path for the
  database. doc-ray's own `backup-scheduler` is **off** (`DOCRAY_BACKUP_ENABLED`
  defaults to false, D-033) — it never produced a usable archive at corpus scale
  and it monopolized a worker slot trying. Switch it on only if you want portable
  snapshots and have accepted the cost. **Spaces is not covered by DO's database
  backups**: enable bucket versioning if the original document bytes need
  point-in-time recovery.
- **Encoder cutovers.** Follow the
  [Corpus Embedding Backfill Runbook](runbook-corpus-embeddings.md) for the
  required backup → enqueue → verify → visual-verify → rollback sequence when
  changing the learned sentence encoder across an existing corpus.
- **Connections.** If the API/workers ever exhaust backends, add DO's PgBouncer
  pool in **session** mode (Step 1) rather than raising doc-ray's pool.
- **Secrets.** App password, Spaces key, JWT/session secrets, and the tunnel token
  live only in `.env.prod` (mode `600`, gitignored). Rotate the Spaces key and
  tunnel token after any exposure.
- **Local resolver (Ollama).** To run the frontier entity resolver on a local
  model instead of the Anthropic API (no key, no per-call cost), layer the ollama
  overlays and route the workers: **`make prod-ollama-up`**. Ollama runs on the
  H200 (CDI reservation — needs the CDI spec from
  [`scripts/provision_host.py`](../scripts/provision_host.py)) with the model kept
  resident (`OLLAMA_KEEP_ALIVE=-1`); the identical PALS verification coerces a
  weaker model's errors to ABSTAIN and routes them to review. Pick the model with
  `DOCRAY_OLLAMA_MODEL`. The server binds loopback only — never public.
- **Batch GPU tier.** For heavy transformer annotation (`en_core_web_trf`), spin
  up an **ephemeral H200 GPU Droplet** that drains the shared job queue, then
  destroy it — see [`gpu-batch-processing.md`](gpu-batch-processing.md). It
  connects to this same managed Postgres + Spaces and runs `make gpu-up`; the
  always-on `worker` handles the low-volume remainder. Bootstrap a fresh GPU box
  with [`scripts/provision_host.py`](../scripts/provision_host.py).
