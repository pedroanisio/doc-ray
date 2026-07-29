---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 (1M context) via Claude Code"
  date: "2026-07-29"
---

# GPU droplet exit plan

## Headline

**The migration has been executed.** doc-ray now runs on a GPU-free droplet in
`tor1`, serving `doc-ray.com` through the Cloudflare Tunnel, with `make prod-check`
green on all 13 probes (2026-07-29). The deferred H200 work list measures **0**
documents.

**The saving is not yet realised.** Billing stops at *destroy*, not at power-off,
and this document cannot observe the old droplet's state. Confirm and destroy
`ml-ai-ubuntu-gpu-6000adax1-48gb-tor1` (`gpu-6000adax1-48gb`, $1.57/h =
**$1,168/month**) in the DigitalOcean control panel. Until that is done the
migration has added cost, not removed it.

Two planned pre-conditions were **never completed** and are now live defects on
the new host — see [Not done](#not-done-carried-forward-as-live-defects).

## Status of the migration

| # | Step | State |
|---|---|---|
| 1 | Clear and verify the H200 work list | **Done** — 0 documents carry `pending-trf-h200` (but read [the gate caveat](#the-readiness-gate-is-fail-open)) |
| 2 | Decide the resolver policy | **NOT DONE** — still unbacked |
| 3 | Add a GPU-free deploy path | **NOT DONE** — `make prod-up` still demands a GPU |
| 4 | Provision a replacement droplet | **Done** — `s-8vcpu-16gb-amd`, not the recommended `c-8` |
| 5 | Move the block volume | **Done** — same volume, verified by serial |
| 6 | Bring the stack up | **Done** — via a hand-assembled compose set, because step 3 was skipped |
| 7 | Cut over ingress | **Done** — 4 edge connections; the old connector was already absent |
| 8 | Verify | **Done** — `make prod-check` 13/13 PASS |
| 9 | **Destroy the GPU droplet** | **OUTSTANDING — this is the entire saving** |

## What was actually provisioned

Measured on the new host, 2026-07-29:

| Item | Planned | Actual |
|---|---|---|
| Droplet | `c-8` (8 *dedicated* vCPU, 16 GB, $168/mo) | `s-8vcpu-16gb-amd` — hostname `ubuntu-s-8vcpu-16gb-amd-tor1`, 8 **shared** AMD vCPU, 15 GB usable |
| Region | `tor1` | `tor1` ✓ |
| Block volume | Detach + reattach, no copy | ✓ — `/dev/sda`, serial **`volume-tor1-1784805288943`**, the same volume named in the pre-migration plan. Now mounted at **`/apps`** (not `/mnt/volume_tor1_1784805288943`), 14 GB used of 100 GB |
| OS | — | Ubuntu 24.04.4 LTS |

The dedicated-core recommendation was not taken. That is a legitimate operator
call — it roughly halves the compute bill — but it means the plan's central
throughput argument ("dedicated cores should hold or improve today's throughput")
no longer applies: the new host has *shared* cores, exactly like the old one.
**The 966 tok/s re-measurement in [Open verification](#open-verification) is
therefore load-bearing, not a formality.**

Host-level detail for the new box lives in [`ENV-PROD.md`](../ENV-PROD.md).

## The readiness gate is fail-open

`scripts/check_gpu_exit_readiness.py` returned `status: ready`,
`pending_documents: 0`, exit `0`. **The verdict is correct, but the method cannot
distinguish "no pending work" from "cannot see any rows."**

The script connects with `DATABASE_URL` — i.e. as the `docray` role — and runs a
bare `SELECT count(DISTINCT dt.document_id) … WHERE t.tag = 'pending-trf-h200'`.
It never assumes `docray_batch` and never sets a tenant context. Measured on this
cluster, 2026-07-29:

| Query context | `documents` | `pending-trf-h200` |
|---|---|---|
| as `docray` (what the gate does) | **0** | **0** |
| as `docray_batch` (`SET ROLE`, `BYPASSRLS`) | **658** | **0** |

`documents` and `document_tags` both carry `relrowsecurity=true` **and**
`relforcerowsecurity=true` (migration 106), and `docray` has `rolbypassrls=false`.
So under RLS the gate sees an empty corpus and reports `ready`. It would print
that same `ready` against an empty database, a wrong `DATABASE_URL`, a dropped
`tags` row, or a revoked grant. Today's `ready` is true only because two
independent facts are *both* zero — the visible-row count and the real pending
count — and the gate cannot tell you which one it measured.

This matters because this gate is the documented authorisation for an
irreversible act: destroying a $1,168/month host. It is also wired into
`make prod-check` as the `gpu_exit` probe, so that PASS inherits the same
weakness.

> ARCHITECTURAL REQUIREMENT (PALS's LAW): absence of output verification is a
> design defect, not a runtime bug. A gate that returns the same answer for
> "verified empty" and "could not see anything" has no verification layer.

**Required fix** (not yet applied): the checker must `SET ROLE docray_batch`
before counting — the pattern `_prod_check_database.check_corpus_rollups` already
uses — and must fail closed on a corpus-wide sanity assertion (e.g. `documents`
count > 0), returning `error` rather than `ready` when the corpus is invisible.

The true corpus state, measured via `docray_batch`: **658 documents**, of which
**636 carry a `spacy/en_core_web_trf@3.8.14` annotation**, and **0 carry
`pending-trf-h200`**. On that evidence the H200 work list is genuinely
discharged, and [`PENDING_TRF_H200.md`](PENDING_TRF_H200.md) has met the
`retire-when` condition in its own frontmatter ("Delete this file in the same
change"). Retire it only after the gate is fixed, so the deletion rests on a
measurement that could have said no.

## Not done — carried forward as live defects

These were steps 2 and 3 of the plan. Skipping them did not block the cutover,
but both are now defects on the running host rather than open plan items.

**1. No GPU-free deploy path (step 3).** `COMPOSE_PROD_OLLAMA`, and therefore
`make prod-up`, still layers `docker-compose.ollama.yml` +
`docker-compose.ollama.gpu.yml`. On a GPU-free host this fails in two ways, both
observed during the cutover:

- `docker-compose.ollama.yml` gives `worker` and `worker-fast` a
  `depends_on: ollama → condition: service_healthy`, so `ollama` is not an inert
  unstarted service — Compose must bring it up first. With the GPU overlay layered
  it demands `nvidia.com/gpu=all`, cannot start, and the workers never start.
- `docker-compose.caption.yml` declares `image: doc-ray-worker` with no `build:`
  stanza, reusing the image the `worker` service builds. Since `worker` uses a
  bare `build: .` with no explicit `image:` name, that image exists only *after*
  `worker` has been built once. On a fresh host Compose reads it as a registry
  reference: `pull access denied for doc-ray-worker`.

The stack is currently up via a hand-assembled command (recorded in
[`ENV-PROD.md`](../ENV-PROD.md)), not via a Make target. **Until a GPU-free target
exists, `make prod-up` is a trap on this host** — the documented bring-up command
is the one that fails.

**2. Resolver policy undecided (step 2).** `DOCRAY_RESOLVER_BACKEND=ollama` is
still set with no GPU and no `ollama` container. Dropping the ollama overlays also
drops the only place `DOCRAY_RESOLVER_BACKEND` and `DOCRAY_OLLAMA_HOST` are
injected into the worker environment — the base compose does not pass them — so
`resolver_factory` falls back to its documented default, `anthropic`, and
`ANTHROPIC_API_KEY` is unset. Entity resolution fails either way; only the error
changes. The original options stand: point it at `anthropic` with a key, or add an
OpenAI-compatible adapter against `https://inference.do-ai.run/v1`, mirroring
`vlm_vllm` — the same pattern that already works for captions here. Ingest,
annotation, embedding and captioning are unaffected.

## Evidence for retiring the GPU (measured 2026-07-28, on the old host)

Retained as the original justification. These are historical measurements taken
on the GPU droplet and are **not** re-measurable now that it no longer runs the
stack.

| Claim | Measurement |
|---|---|
| GPU is idle | `nvidia-smi`: **0 % utilisation, 2 MiB used of 49,140 MiB**, no compute processes |
| Workers cannot reach the GPU | In `doc-ray-worker-1`: `torch.cuda.is_available()` → **False** |
| spaCy already runs on CPU | `spacy.prefer_gpu()` → **False**; `en_core_web_trf` measured **966 tok/s** with `CUDA_VISIBLE_DEVICES=""` and **959 tok/s** without — identical, so the GPU was never in the path |
| Vision/text encoders already on CPU | `vision_encoder_openclip` and `text_encoder_sentence_transformers` select `cuda` only when `torch.cuda.is_available()`, which is False in the worker |
| The frontier resolver never ran | **0 of 601** `canonicalize` jobs in 30 days had `resolve` enabled |
| Captions no longer need local GPU | Tier-3 moved to DigitalOcean serverless inference (`nemotron-nano-12b-v2-vl`) |

Only the `ollama` container was ever granted the GPU. Its two consumers were
Tier-3 captions (now serverless) and the frontier resolver (never enabled).

Old-host envelope for comparison: 8 vCPU, 62 GB RAM (10 GB used), 100 GB volume
(14 GB used), 48 GB VRAM (**0** used). Weekly compute by stage, succeeded jobs
over 7 days: annotate 21.7 h, visual_embed 10.5 h, visual_extract 9.8 h,
score_mentions 6.0 h, canonicalize 1.9 h, semantic_enrich 1.4 h, ingest 1.2 h,
semantic_embed 0.9 h.

## Open verification

Neither of these blocks the destroy, but both should be measured before the
migration is called complete.

- **Re-measure annotate throughput** against the 966 tok/s baseline. The plan
  assumed dedicated `c-8` cores; shared cores were provisioned instead, so the
  "hold or improve" prediction is untested. If throughput regresses materially,
  the remedy is more `worker` replicas or a size bump — both far below GPU pricing.
- **Re-measure memory under load.** At idle with the full stack up (api + webapp +
  worker + worker-fast + 6 × worker-caption + mcp + cloudflared) the new host sits
  at ~3 GB used / ~12 GB available. The general workers grow substantially once
  annotation loads spaCy and torch, and this box has 15 GB where the old one had
  62 GB.

## Risks

- **Dual ingress.** `CLOUDFLARE_TUNNEL_TOKEN` was copied to the new host, and a
  token identifies a tunnel, not a machine. The old droplet was confirmed absent
  from the tunnel before cutover (`doc-ray.com` returned **530**/Error 1033, and
  the new host then registered 4 edge connections cleanly). But powering the old
  droplet back on with its copy of `.env.prod` re-registers it against the *same*
  tunnel, and Cloudflare will split traffic between two app stacks sharing one
  database. Destroying it closes this permanently; until then, do not power it on.
- **Ingestion volume grows.** Recent load is 35–319 documents/day. If CPU annotate
  becomes the bottleneck, scale `worker` replicas (spaCy parallelises across
  processes) or move up a droplet size.
- **A future stage genuinely needs a GPU.** Serverless inference covers the VLM
  path and the pattern extends to embeddings. Note that re-embedding would
  invalidate ~922,590 sentence and ~63,896 image vectors — a separate, deliberate
  decision, not part of this migration.

## What this does not change

Tier-3 captioning stays on DigitalOcean serverless. Its known defect — a caption
job wraps every image of a document in one transaction, so a mid-document failure
discards all paid-for API calls and restarts from image 0 — is independent of this
migration and should be fixed regardless.
