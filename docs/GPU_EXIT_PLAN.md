---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 (1M context) via Claude Code"
  date: "2026-07-28"
---

# GPU droplet exit plan

## Headline

**Nothing on this host uses the GPU.** The exit is a droplet resize, not a
re-architecture. Measured saving: **~$1,000/month (~$12,000/year)**.

The host is `ml-ai-ubuntu-gpu-6000adax1-48gb-tor1` (RTX 6000 Ada 48 GB),
`gpu-6000adax1-48gb`, **$1.57/h = $1,168/month**.

## Evidence (measured 2026-07-28)

| Claim | Measurement |
|---|---|
| GPU is idle | `nvidia-smi`: **0 % utilisation, 2 MiB used of 49,140 MiB**, no compute processes |
| Workers cannot reach the GPU | In `doc-ray-worker-1`: `torch.cuda.is_available()` → **False** |
| spaCy already runs on CPU | `spacy.prefer_gpu()` → **False**; `en_core_web_trf` measured **966 tok/s** with `CUDA_VISIBLE_DEVICES=""` and **959 tok/s** without — identical, so the GPU was never in the path |
| Vision/text encoders already on CPU | `vision_encoder_openclip` and `text_encoder_sentence_transformers` select `cuda` only when `torch.cuda.is_available()`, which is False in the worker |
| The frontier resolver never ran | **0 of 601** `canonicalize` jobs in 30 days had `resolve` enabled |
| Captions no longer need local GPU | Tier-3 moved to DigitalOcean serverless inference (`nemotron-nano-12b-v2-vl`) |

Only the `ollama` container was ever granted the GPU (via
`docker-compose.ollama.gpu.yml`). Its two consumers were Tier-3 captions (now
serverless) and the frontier resolver (never enabled). It currently holds no
model in VRAM.

### The one standing reason to keep a GPU is already discharged

`docs/PENDING_TRF_H200.md` defers a set of oversized documents to a large-VRAM
run, because `en_core_web_trf` OOMed on an 8 GB dev card (peak VRAM scales with
document length). That was the last documented argument for large-VRAM hardware.

**Zero documents now carry the `pending-trf-h200` tag**, which is exactly that
document's own stated `retire-when` condition. Separately, the constraint cannot
recur after this migration: the OOM was a *VRAM* ceiling, and CPU annotation has
no VRAM ceiling — an oversized book annotates against host RAM, slowly but
without failing. Leaving the GPU removes the problem the deferral existed for.

(29 documents remain unannotated for unrelated reasons; none are tagged.)

## Current resource envelope

| Resource | Provisioned | Actually used |
|---|---|---|
| vCPU | 8 | ~53 job-hours/week ≈ 7.6 h/day of single-lane compute |
| RAM | 62 GB | 10 GB |
| Disk (block volume) | 100 GB | 14 GB |
| VRAM | 48 GB | **0** |

Weekly compute by stage (succeeded jobs, 7 days): annotate 21.7 h,
visual_embed 10.5 h, visual_extract 9.8 h, score_mentions 6.0 h, canonicalize
1.9 h, semantic_enrich 1.4 h, ingest 1.2 h, semantic_embed 0.9 h.

## State that must migrate

Effectively none — the host is stateless.

- **Postgres**: managed cluster (`db-pgsql-tor1-…`), off-box already.
- **Object storage**: Spaces (`nyc3`), off-box already.
- **Local docker volumes**: `doc-ray_ollama-models` (disposable — the models go
  away with Ollama), `doc-ray_pgdata` (71 MB, unused in prod), `doc-ray_miniodata`
  (unused in prod).
- **On the block volume**: the git checkout at
  `/mnt/volume_tor1_1784805288943/doc-ray` (14 GB). The volume is region-bound to
  `tor1`, so the replacement droplet must be in `tor1` — which is also where the
  managed database lives.

## Replacement sizing

Candidates in `tor1`, priced 2026-07-28 via the DigitalOcean sizes API:

| Slug | vCPU | RAM | $/month | Note |
|---|---|---|---|---|
| `c-8` | 8 dedicated | 16 GB | **168** | CPU-optimised; dedicated cores suit CPU-bound spaCy |
| `s-8vcpu-32gb-amd` | 8 shared | 32 GB | 168 | More RAM, shared cores |
| `s-8vcpu-16gb` | 8 shared | 16 GB | 96 | Cheapest; least headroom |

**Recommended: `c-8`.** The dominant workload (`en_core_web_trf`, PyMuPDF
rendering, OpenCLIP on CPU) is CPU-bound, and the current droplet's 8 vCPU are
*shared*. Dedicated cores should hold or improve today's throughput. RAM at
16 GB leaves 6 GB of headroom over current usage; choose `s-8vcpu-32gb-amd`
instead if the caption/worker pools are to be scaled well beyond present levels.

## Migration steps

1. **Decide the resolver policy.** `DOCRAY_RESOLVER_BACKEND=ollama` is set but
   has never been exercised. Either set it to `anthropic` (the adapter exists),
   or add an OpenAI-compatible resolver adapter pointing at
   `https://inference.do-ai.run/v1` — the same shape as `vlm_vllm`. Until then,
   dropping Ollama removes an unused capability, not a working one.
2. **Add a GPU-free deploy path.** `COMPOSE_PROD_OLLAMA` (and therefore
   `make prod-up`) currently layers `docker-compose.ollama.yml` +
   `docker-compose.ollama.gpu.yml`. Introduce a compose set without them so the
   stack never asks for a device the host lacks.
3. **Provision** a `c-8` droplet in `tor1`.
4. **Move the block volume**: detach from the GPU droplet, attach to the new one
   (same region, so the checkout moves intact with no copy).
5. **Bring the stack up**: copy `.env.prod` (secrets — never in git), build
   images, `up -d` with the GPU-free compose set.
6. **Cut over ingress**: the Cloudflare tunnel follows `CLOUDFLARE_TUNNEL_TOKEN`
   in `.env.prod`; start `cloudflared` on the new host and stop it on the old one
   so only one replica serves the hostname.
7. **Verify** before destroying anything: `make prod-check`, a webapp login, an
   MCP read, and one end-to-end ingest.
8. **Destroy the GPU droplet.** Billing stops at destroy, not at power-off.

## Risks and how they are handled

- **Throughput regression on shared→dedicated CPU.** Low: annotate already runs
  on CPU at a measured 966 tok/s, and `c-8` gives dedicated cores. Re-measure
  with the same benchmark after cutover and compare against 966 tok/s.
- **Ingestion volume grows.** Recent load is 35–319 documents/day. If CPU
  annotate becomes the bottleneck, scale `worker` replicas (spaCy parallelises
  across processes) or move up a droplet size — still an order of magnitude
  below the GPU droplet's price.
- **A future stage genuinely needs a GPU.** Serverless inference now covers the
  VLM path, and the same pattern extends to embeddings if ever required. Note
  that re-embedding would invalidate ~922,590 sentence and ~63,896 image vectors,
  so that is a separate, deliberate decision — not part of this migration.
- **Dual ingress during cutover.** Two `cloudflared` replicas serving one
  hostname split traffic; stop the old one before or immediately after starting
  the new one.

## What this does not change

Tier-3 captioning stays on DigitalOcean serverless. Its known defect — a caption
job wraps every image of a document in one transaction, so a mid-document failure
discards all paid-for API calls and restarts from image 0 — is independent of
this migration and should be fixed regardless.
