---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-23"
---

# ENVIRONMENT — this host is **production**

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](./DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

There is no separate "prod box" to promote to. Commands run here act on the live
service at `https://doc-ray.com` and on data belonging to real users. Read
[Rules of engagement](#rules-of-engagement) before running anything stateful.

---

## Topology

Nothing that holds data runs on this host. The droplet runs **stateless
containers only**; the corpus lives in managed services, and the public edge is
a Cloudflare Tunnel with **no inbound ports open on the droplet**.

```
                    Cloudflare edge (TLS terminates here)
                              │  outbound-only tunnel
                    ┌─────────┴──────────┐
                    │ docray-cloudflared │
                    └─────────┬──────────┘
   loopback-bound app containers (compose project `doc-ray`)
     api :8000 · webapp :5001 · mcp :8080 · workers · backup-scheduler · ollama
                    │                    │                    │
       DO Managed Postgres        DO Spaces (S3)         SMTP2GO relay
         (:25060, TLS)             (nyc3 bucket)          (STARTTLS)
```

| Concern | Provider | Endpoint / identity | Notes |
| --- | --- | --- | --- |
| Relational store | DigitalOcean Managed PostgreSQL | port `25060`, `sslmode=require`, database `docray` | Cluster hostname is `…tor1…` (Toronto); credentials in `.env.prod` only |
| Blob store | DigitalOcean Spaces | `https://nyc3.digitaloceanspaces.com`, bucket `doc-ray-prd` | Original document bytes, content-addressed |
| Outbound mail | SMTP2GO | `mail.smtp2go.com:2525`, STARTTLS, from `noreply@doc-ray.com` | **Not 587** — see [Known constraints](#known-constraints) |
| Public ingress | Cloudflare Tunnel | `doc-ray.com`, `admin.doc-ray.com` | Token-based; routing is dashboard-side, not in this repo |
| Entity resolution | Local Ollama on GPU | `http://ollama:11434` | In-network only; not exposed publicly |

Schema version is authoritative in the database itself — read it, never assume:
`SELECT docray_schema_version();` (or the `database` line of `make prod-check`).

## Host

| | |
| --- | --- |
| OS | Ubuntu 22.04.5 LTS |
| CPU / RAM | 8 vCPU / 62 GB |
| GPU | NVIDIA RTX 6000 Ada Generation, 48 GB (driver 580.173.02) |
| Repo | `/mnt/volume_tor1_1784805288943/doc-ray` on a 100 GB attached volume |
| Alias | `/apps/doc-ray` is a **symlink to the same checkout** — one working tree, two paths |
| Compose project | `doc-ray` (identical from either path, so containers never fork) |

## Configuration

`.env.prod` is the only production config file. It is **gitignored** and holds
every credential; `.env` is the development file and must never be used to reach
these services. Compose is always invoked with `--env-file .env.prod`, which
*replaces* `.env` for interpolation — the make targets do this for you.

Every variable is defined once in [`config/env.registry.yaml`](config/env.registry.yaml);
`.env.prod` is built from the generated [`.env.prod.example`](.env.prod.example)
template. Validate this host's file with `python scripts/check_env.py .env.prod --prod`.
Full guide: [docs/environment-configuration.md](docs/environment-configuration.md).

Overlays that compose the production stack:

| File | Adds |
| --- | --- |
| `docker-compose.yml` | Base service definitions |
| `docker-compose.prod.yml` | Repoints every service at the managed cluster + Spaces, gates the local `postgres`/`minio` out onto a never-enabled `local-infra` profile, binds app ports to loopback |
| `docker-compose.ollama.yml` + `.ollama.gpu.yml` | Local Ollama resolver on the GPU |
| `docker-compose.mcp.yml` | The MCP server for AI agents (`depends_on: api`, so it **cannot** be run standalone — use `make mcp-up`) |
| `docker-compose.cloudflared.yml` | The tunnel. Kept separate because its token is a hard `${…:?}` requirement that would break token-less invocations |

## Verifying

```bash
make prod-check              # every probe, ending in a real SMTP delivery
make prod-check NO_SEND=1    # same, but the relay check delivers nothing
```

Covers config, security posture, the managed cluster **and migration lockstep**,
Spaces, `/readyz`, the webapp, MCP (loopback and public `/mcp`), tunnel edge
connections, and the public URL through Cloudflare. Exit `0` clean, `1`
warnings, `2` failures. Run it after every deployment — it is the only thing
that exercises outbound mail, and a broken relay is otherwise invisible until a
real user's registration fails.

## Rules of engagement

1. **Assume every command is live.** `DATABASE_URL` here points at the managed
   production cluster; `scripts/truncate_corpus.py`, `make truncate-db`, and any
   ad-hoc `psql` act on real user data.
2. **The data is off-box.** `make prod-down` stops containers and destroys
   nothing — the corpus is in the managed cluster and Spaces. Recovery from a
   bad *write*, however, depends on backups, not on the containers.
3. **Migrations are one-way.** `make prod-migrate` / `prod-migrate-host` apply
   forward only. A cluster behind the running code is a `prod-check` failure,
   not a warning.
4. **The test stack runs on this same host.** `docker-compose.test.yml` starts
   its own throwaway `docray-test-postgres` / `docray-test-minio`. Never confuse
   a `docray-test-*` container with a production one, and never point the test
   stack at `.env.prod`.
5. **Restarting is not free.** `make prod-refresh-ui` recreates `api` + `webapp`;
   the public site 502s through the tunnel until they are healthy again.
6. **Env changes need a container recreate.** Editing `.env.prod` changes
   nothing for a running process — env is read at start.
7. **Never commit `.env.prod`, tokens, or relay credentials.** Document
   endpoints and identities here; keep secrets in the env file.

## Known constraints

- **DigitalOcean blocks outbound SMTP (25 / 465 / 587).** A perfectly correct
  SMTP2GO configuration on 587 fails with a bare `timed out` that reads like bad
  credentials. SMTP2GO also serves STARTTLS on **2525** and **8025**, which are
  not blocked — production uses `DOCRAY_SMTP_PORT=2525`.
- **Cloudflare route order decides `/mcp`.** Tunnel rules match top-to-bottom,
  first match wins: `doc-ray.com /mcp → http://mcp:8080` must sit **above** the
  `doc-ray.com * → webapp:5001` catch-all, or `/mcp` 302s to the login page.
- **The MCP server rejects unknown `Host` headers.** Its SDK's DNS-rebind guard
  answers **421** unless `DOCRAY_PUBLIC_BASE_URL` (or `DOCRAY_MCP_ALLOWED_HOSTS`)
  allow-lists the public hostname.
- **A dropped tunnel surfaces as Cloudflare Error 1033 (HTTP 530).** The origin
  is fine; nothing is serving the hostname. `make prod-tunnel` restores it.
- **Cross-region storage.** The database cluster and the Spaces bucket are not
  in the same region, so blob round-trips carry inter-region latency.

## See also

- [docs/production-setup.md](docs/production-setup.md) — provisioning this
  environment from scratch, step by step
- [docs/mcp-service.md](docs/mcp-service.md) — the agentic surface and its
  production gotchas
- [docs/architecture.md](docs/architecture.md) — what the services actually do
- [CLAUDE.md](CLAUDE.md) — how to work in this repository
