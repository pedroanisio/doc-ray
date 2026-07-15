---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-15"
---

# Ephemeral GPU Batch Processing (H200)

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

This runbook adds an **ephemeral, cost-aware GPU tier** to the production
deployment in [`production-setup.md`](production-setup.md). A DigitalOcean H200
GPU Droplet is **spun up on demand** to drain a batch of ingestion + NLP
annotation jobs, then **destroyed** — it is never left running. It complements
[`PENDING_TRF_H200.md`](PENDING_TRF_H200.md), which covers *what to run on the
H200* (the deferred `en_core_web_trf` documents); this doc covers *the spin-up /
spin-down lifecycle* around it.

```
        always-on tier                    ephemeral batch tier
   ┌───────────────────────┐         ┌─────────────────────────────┐
   │ Droplet: api + worker │         │ H200 GPU Droplet (transient)│
   │  (CPU, low volume)    │         │  worker × N (en_core_web_trf│
   └──────────┬────────────┘         │  on GPU)                    │
              │                       └──────────────┬──────────────┘
              │   both drain the SAME Postgres job queue
              ▼                                      ▼
        ┌────────────────────────────────────────────────┐
        │ DO Managed Postgres — jobs (FOR UPDATE SKIP     │
        │ LOCKED) + heartbeat/reaper   ·   Spaces blobs   │
        └────────────────────────────────────────────────┘
```

---

## ⚠ Cost model — read this first

- **NVIDIA H200×1 ≈ $3.44/hour**, billed **per second** (60-second minimum).
  *(Verify current rates on DigitalOcean's [GPU Droplets pricing](https://www.digitalocean.com/pricing/gpu-droplets) — GPU prices move.)*
- **A powered-off GPU Droplet still bills** — resources stay reserved. "Spin
  down" must mean **`doctl compute droplet delete`**, not power-off. This is the
  single most important cost rule here.
- Therefore the lifecycle is **enqueue → create → drain → destroy**. The droplet
  exists only while the queue is non-empty.

## Why ephemeral is safe (the queue does the work)

You can destroy the GPU droplet at any moment — mid-job included — without losing
or corrupting work. Four properties guarantee it:

1. **Coexistence.** The queue claims jobs with `FOR UPDATE SKIP LOCKED`, so the
   ephemeral GPU workers and the always-on CPU worker never process the same job.
   Add or remove GPU workers freely.
2. **Interruption-safe.** Each running job holds a time-boxed lease with a
   heartbeat. If a worker vanishes (droplet destroyed), the reaper
   (`queue_postgres.reap_stale`) releases the job back to `queued` **without
   spending a retry attempt** — the next worker picks it up. Abrupt teardown is a
   supported operation, not a failure mode.
3. **Graceful drain.** On `SIGTERM`/`SIGINT` the worker finishes (or cleanly
   releases) its in-flight job before exiting, so a *clean* shutdown wastes no
   compute.
4. **Idempotent ingest.** Blobs are content-addressed; re-ingesting identical
   bytes is a no-op (`documents.content_sha256` is `UNIQUE`). A half-finished
   batch is safe to re-run.

## H200 sizing

The local 8 GB dev card runs `en_core_web_trf` only up to ~120k tokens per
document and fits exactly **one** worker (see [`PENDING_TRF_H200.md`](PENDING_TRF_H200.md)
for the measured ceilings). The **H200's 141 GB VRAM removes both limits**: no
per-document token cap, and room for **many** parallel trf workers (~6 GB peak
each).

Pick the worker replica count `N` as the **minimum** of three budgets:

| Budget | Bound |
| --- | --- |
| VRAM | 141 GB ÷ ~6 GB ≈ 20 (theoretical ceiling) |
| GPU droplet vCPUs | each worker also does CPU work (extraction, tokenization glue) — don't exceed the droplet's core count |
| Managed Postgres backends | each worker holds ~1 connection; keep `N` + always-on workers well under the cluster's backend limit (~97 on the 8 GB plan) |

Start at **`--scale worker=4`–`8`** and tune from GPU/CPU utilisation. The GPU
overlay already sets `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`
(harmless on the H200) and releases device memory between documents.

## Prerequisites

- The always-on production stack is up ([`production-setup.md`](production-setup.md)):
  Managed Postgres + Spaces reachable, `.env` populated.
- `doctl` authenticated (`doctl auth init`) with GPU-Droplet quota.
- An SSH key registered in the account; the **VPC UUID** of the region so the GPU
  droplet reaches Postgres/Spaces over the private network (and add its IP/VPC to
  the cluster's **Trusted Sources**).
- A GPU-ready base image with NVIDIA driver ≥ the torch CUDA build (**CUDA 13.x**,
  matching `cupy-cuda13x`) and `nvidia-container-toolkit` CDI specs in
  `/etc/cdi`. Verify on the droplet:
  ```bash
  docker run --rm --device nvidia.com/gpu=all nvidia/cuda:13.0-base-ubuntu24.04 nvidia-smi -L
  ```

## Lifecycle

### Phase 1 — Enqueue the batch (from the always-on node)

Fill the queue **before** the GPU exists, so the droplet does real work from the
first second it bills. Pick the recipe that matches the batch:

```bash
set -a; source .env; set +a

# (a) Fresh documents from Spaces — full pipeline (ingest -> annotate ->
#     semantic_enrich -> canonicalize) enqueued per new doc:
python scripts/ingest_from_spaces.py            # idempotent; skips already-loaded

# (b) The deferred oversized documents (tag 'pending-trf-h200') — enqueue an
#     annotate job each. See PENDING_TRF_H200.md for the exact snippet.

# (c) Re-annotate / re-enrich the whole corpus:
python -m doc_ray.entrypoints.reannotate --all          # spaCy layers
python -m doc_ray.entrypoints.annotate_semantic --all   # deterministic enrichment
```

Confirm the queue depth you're about to burn GPU on:

```bash
psql "$DATABASE_URL" -Atc \
  "SELECT status, count(*) FROM jobs WHERE status IN ('queued','running') GROUP BY status;"
```

### Phase 2 — Spin up the H200

```bash
doctl compute size list | grep -i gpu          # find the H200 size slug
doctl compute droplet create docray-gpu-batch \
  --size <H200_SIZE_SLUG> \
  --image <GPU_AI_READY_IMAGE> \
  --region nyc3 \
  --vpc-uuid <VPC_UUID> \
  --ssh-keys <SSH_KEY_ID> \
  --wait
```

Deploy the checkout + production `.env` (same `DATABASE_URL`/`S3_*` as the
always-on node — this droplet runs **only** the worker), then start the GPU
workers:

```bash
GPU_IP=$(doctl compute droplet get docray-gpu-batch --format PublicIPv4 --no-header)
ssh root@"$GPU_IP" '
  cd /opt/doc-ray &&
  DOCRAY_WORKERS=8 make gpu-up
'
# make gpu-up expands to (the prod overlay is REQUIRED — without it the worker
# uses the base compose'\''s local @postgres:5432 and never reaches the managed
# cluster; the gpu overlay adds the CDI GPU reservation):
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
#     -f docker-compose.gpu.yml up -d --build --scale worker=8 worker
```

### Phase 3 — Drain, then destroy

The worker **polls forever** (there is no built-in "exit when empty" mode — see
*Enhancement* below), so spin-down is driven externally: wait until the queue has
been empty for a few consecutive checks, drain gracefully, then **destroy**.

```bash
# Wait for the queue to stay empty across 3 checks (guards against a job briefly
# between states), then tear down. Destroying stops billing.
empty=0
until [ "$empty" -ge 3 ]; do
  sleep 60
  depth=$(psql "$DATABASE_URL" -Atc \
    "SELECT count(*) FROM jobs WHERE status IN ('queued','running');")
  if [ "$depth" = "0" ]; then empty=$((empty+1)); else empty=0; fi
  echo "queue depth=$depth  empty-streak=$empty"
done

ssh root@"$GPU_IP" 'cd /opt/doc-ray && docker compose stop worker'   # graceful drain
doctl compute droplet delete docray-gpu-batch -f                     # BILLING STOPS HERE
```

`docker compose stop` sends `SIGTERM` for a clean drain; even if you skip it and
delete outright, the reaper releases any in-flight job unharmed. Confirm the
batch afterwards (e.g. for the `pending-trf-h200` set, the tag-cleanup query in
[`PENDING_TRF_H200.md`](PENDING_TRF_H200.md) should return 0).

## Cost estimate

Cost ≈ **wall-clock hours the droplet exists × ~$3.44** (H200×1). Because billing
is per-second and starts at create / stops at delete, the levers are: enqueue
first (no idle GPU), raise `N` to shorten wall-clock (throughput up, hours down),
and never leave it powered-off. Example: a batch that drains in 40 minutes at
`scale=8` costs ≈ $2.30.

## Recommended enhancement — a `--drain` worker mode

Today's cost-aware spin-down relies on an **external** queue-depth poll. A cleaner
design is a worker flag that exits `0` after the queue has been empty for K polls
(`--drain --idle-exit K`). It would let a single orchestration command run the
worker to completion and return, so the create→drain→destroy loop needs no
side-channel SQL. This is a small, well-scoped change to
`src/doc_ray/entrypoints/worker.py`; track it as a backlog item if the ephemeral
GPU tier becomes routine.

## Operations notes

- **Always destroy, never stop.** The one rule that dominates GPU cost.
- **Trusted Sources.** Add the GPU droplet (or its VPC) to the cluster's allow
  list before Phase 2, or connections fail closed.
- **Connection budget.** `N` GPU workers + the always-on `worker`/`worker-fast` +
  the API pool must fit the managed cluster's backend limit; lower `N` before
  raising `DOCRAY_API_POOL_MAX_SIZE`.
- **Ingestion is CPU-bound; annotation is GPU-bound.** The H200 accelerates the
  `annotate` (trf) stage; PDF extraction still runs on the droplet's CPUs, so
  size `N` against both GPU and vCPU headroom.
