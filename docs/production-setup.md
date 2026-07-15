---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-15"
---

# Production Setup — Managed Postgres + Spaces

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

This runbook stands up a production doc-ray on **managed** DigitalOcean
services: a **Managed PostgreSQL** cluster for the database, **Spaces** for the
content-addressed blob store, and a **Droplet** running only the stateless app
services (GraphQL API + worker). It complements
[`deploy-digitalocean.md`](deploy-digitalocean.md), which runs Postgres and MinIO
as containers on the Droplet — use *this* guide when you want the database and
object storage managed rather than self-hosted.

```
                         ┌──────────────────────────┐
   HTTPS (Caddy/nginx) ─►│  Droplet                 │
                         │   api + worker           │
                         │   (stateless, no DB/blob)│
                         └──────┬─────────────┬─────┘
             DATABASE_URL (TLS) │             │ S3_ENDPOINT (TLS)
                                ▼             ▼
              ┌────────────────────────┐  ┌───────────────────────┐
              │ DO Managed PostgreSQL  │  │ DO Spaces (S3)        │
              │ pgvector · TLS · PITR  │  │ content-addressed blobs│
              └────────────────────────┘  └───────────────────────┘
```

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

---

## Prerequisites

- The Managed PostgreSQL cluster is created (this guide assumes **PostgreSQL 16**,
  `sslmode=require`, region **nyc3**). Have the connection details ready:
  `host`, `port` (direct: `25060`), `database` (`defaultdb`), admin user
  (`doadmin`), and password.
- A Droplet (or DO App Platform service) to run the app — see
  [`deploy-digitalocean.md`](deploy-digitalocean.md) steps 1–5 for the base
  Droplet, Docker, firewall, and checkout. Do **not** start the `postgres` or
  `minio` containers on it.
- `psql` available locally or on the Droplet for one-time cluster setup.

---

## Step 1 — Prepare the Managed Postgres cluster

Connect as the admin user and create a **dedicated database and least-privilege
role** for doc-ray (do not run the app as `doadmin`), then enable pgvector.

```bash
ADMIN_URL="postgresql://doadmin:<ADMIN_PW>@<CLUSTER_HOST>:25060/defaultdb?sslmode=require"

# 1. Dedicated role + database
psql "$ADMIN_URL" <<'SQL'
CREATE ROLE docray LOGIN PASSWORD '<STRONG_APP_PASSWORD>';
CREATE DATABASE docray OWNER docray;
SQL

# 2. Enable pgvector INSIDE the docray database (doc-ray requires it)
psql "postgresql://doadmin:<ADMIN_PW>@<CLUSTER_HOST>:25060/docray?sslmode=require" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

> DigitalOcean Managed PostgreSQL supports the `vector` extension at no extra
> cost. If `CREATE EXTENSION vector` errors, enable it from the cluster's
> **Settings → Extensions** panel first.

**Trusted sources (firewall):** in the cluster's **Settings → Trusted Sources**,
restrict inbound to the Droplet (or its VPC) only. Prefer the **VPC network**
endpoint when the Droplet is in the same region/VPC — traffic stays private and
you avoid public-internet egress.

**Connection pooling — use the direct endpoint.** doc-ray already pools
connections **client-side** (`psycopg_pool`, `DOCRAY_API_POOL_MAX_SIZE`), and its
backend footprint is small (API pool + worker ≪ the ~97 backends an 8 GB cluster
allows). Point `DATABASE_URL` at the **direct** port `25060`. Only introduce
DigitalOcean's PgBouncer pool if connection pressure later demands it — and if you
do, use **session** mode, because `psycopg3` auto-prepares statements and
transaction-mode pooling breaks prepared statements unless the PgBouncer build
supports named prepared statements.

## Step 2 — Provision Spaces as the blob store

Original document bytes are content-addressed in object storage. In production use
a **dedicated Spaces bucket** (separate from any source/ingest bucket).

1. Create a Spaces bucket, e.g. `docray-prod-<region>`, with **File Listing:
   Restricted**.
2. Create a **bucket-scoped access key** (Applications & API → Spaces Keys →
   Create Access Key), Read/Write, scoped to that bucket. Copy the secret once.
3. Note the S3 endpoint for the region, e.g. `https://nyc3.digitaloceanspaces.com`.

## Step 3 — Production environment file

On the Droplet, in the checkout, write `.env` (mode `600`). Point the database and
object-store settings at the managed services; leave the app-tuning defaults.

```bash
cat > .env <<'EOF'
# --- Managed Postgres (direct endpoint, TLS required) ---
DATABASE_URL=postgresql://docray:<STRONG_APP_PASSWORD>@<CLUSTER_HOST>:25060/docray?sslmode=require
DOCRAY_API_POOL_MIN_SIZE=1
DOCRAY_API_POOL_MAX_SIZE=10
DOCRAY_API_POOL_TIMEOUT=10
DOCRAY_API_STATEMENT_TIMEOUT_MS=15000

# --- Spaces (managed blob store) ---
S3_ENDPOINT=https://nyc3.digitaloceanspaces.com
S3_BUCKET=docray-prod-nyc3
S3_ACCESS_KEY=<SPACES_KEY_ID>
S3_SECRET_KEY=<SPACES_SECRET>
# Optional: request server-side encryption on writes
# S3_SERVER_SIDE_ENCRYPTION=AES256

# --- API auth (fail closed in production) ---
DOCRAY_API_ALLOW_ANONYMOUS=false
DOCRAY_API_TOKENS=            # set in Step 5 after the admin principal exists
DOCRAY_API_DEBUG=             # leave UNSET in production (no introspection)

# --- App-native backups (optional; DO PITR is the primary safety net) ---
DOCRAY_BACKUP_SCHEDULE_SECONDS=86400
DOCRAY_BACKUP_RETENTION_COUNT=7
DOCRAY_BACKUP_RETENTION_DAYS=30
EOF
chmod 600 .env
```

Notes:
- `S3_ENDPOINT` is `https://` — no `S3_ALLOW_INSECURE_ENDPOINT` needed (that flag
  is only for local plaintext MinIO).
- doc-ray's backup blobs land in `S3_BUCKET` too; DO Spaces has no per-object
  listing cost, so this is fine.

## Step 4 — Apply the schema and load data

All **container** commands use the production overlay
[`docker-compose.prod.yml`](../docker-compose.prod.yml): the base compose runs a
local Postgres + MinIO and hardcodes their URLs over `.env`, so the overlay
repoints every app service at the managed cluster + Spaces and severs the
dependency on those local containers (which then never start). Keep a dummy
`POSTGRES_PASSWORD=<any>` line in `.env` — Compose interpolates the base file at
load even though that container is unused.

Run the migration runner against the managed cluster (creates all tables,
indexes, triggers), then load the corpus.

```bash
set -a; source .env; set +a          # DATABASE_URL, S3_*, dummy POSTGRES_PASSWORD

# Host CLI reads DATABASE_URL from .env directly (no compose needed):
python -m doc_ray.entrypoints.migrate            # re-run should report "0 applied"
#   containerised equivalent:
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate
```

Load the corpus by whichever fits:
- **Fresh ingest from Spaces** — idempotent, content-addressed
  (`scripts/ingest_from_spaces.py`); re-runnable and safe.
- **Restore a snapshot** — `createBackup` on the old database, then `restoreBackup`
  on production (schema versions must match).
- **`pg_dump`/`pg_restore`** — or DigitalOcean's *"Migrate from an existing
  PostgreSQL database"* flow for a one-shot cutover.

## Step 5 — Start services, enable auth

Start only the stateless services via the overlay — the local `postgres`/`minio`
never start because the overlay severed their dependencies:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  up -d api worker worker-fast backup-scheduler
```

Create the admin token from the seeded `bootstrap-admin` principal and fail
closed (mirrors [`deploy-digitalocean.md`](deploy-digitalocean.md) step 8):

```bash
ADMIN_PRINCIPAL_ID="$(psql "$DATABASE_URL" -Atc \
  "SELECT id FROM principals WHERE display_name='bootstrap-admin';")"
API_TOKEN="$(openssl rand -hex 32)"
sed -i "s|^DOCRAY_API_TOKENS=.*|DOCRAY_API_TOKENS=$API_TOKEN=$ADMIN_PRINCIPAL_ID:read+write|" .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api worker worker-fast backup-scheduler
printf 'Store this API token in your password manager: %s\n' "$API_TOKEN"
```

Put HTTPS in front (Caddy or the bundled nginx) as in the base guide, and keep
the API bound to loopback behind it.

## Step 6 — Verify

```bash
curl -fsS http://127.0.0.1:8000/readyz          # expect "ready":true (DB + S3 reachable)
psql "$DATABASE_URL" -c "SELECT docray_schema_version();"
python -m doc_ray.entrypoints.db_health         # instance health against the managed cluster
python -m doc_ray.entrypoints.corpus_health     # document/job content health
```

`/readyz` returning `true` confirms the app reached both the managed database
(over TLS) and Spaces. `db_health` reports size/connection pressure so you can
watch the disk-headroom concern from Step 0.

---

## Operations

- **Disk headroom.** Watch `db_health` (or DO's dashboard) for disk usage. The
  32 GB → ~60 GB annotation growth means **resize disk to ~120 GiB before the
  corpus finishes annotating.** Disk resizes are in place; do it early.
- **Backups.** DigitalOcean provides automated daily backups + point-in-time
  recovery on the managed cluster — this is the primary recovery path. doc-ray's
  `backup-scheduler` produces portable, object-store-hosted snapshots on top;
  keep it for portability, not durability.
- **Connections.** If the API/worker ever exhaust backends, add DO's PgBouncer
  pool in **session** mode (see Step 1) rather than raising doc-ray's pool.
- **Read-only nodes.** Available once the cluster has ≥1 backup; useful later if
  read (GraphQL) load grows — point a read replica at `ReadRepository` traffic.
- **Secrets.** The app password and Spaces secret live only in `.env` (mode
  `600`); rotate the Spaces key after any exposure.
- **Batch GPU tier.** For heavy NLP annotation (transformer `en_core_web_trf`),
  spin up an **ephemeral H200 GPU Droplet** that drains a batch of the shared job
  queue and is then destroyed — see
  [`gpu-batch-processing.md`](gpu-batch-processing.md). It connects to this same
  managed Postgres + Spaces; the always-on `worker` handles the low-volume
  remainder.
