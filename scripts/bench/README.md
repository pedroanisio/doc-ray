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

# Resolver backend benchmark — which Ollama model replaces Anthropic?

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

[← back to repo root README](../../README.md)

---

## What is actually being benchmarked

The Stage-3 frontier resolver ([`resolver_factory`](../../src/doc_ray/adapters/resolver_factory.py))
does **one narrow, structured job**, identical on both backends: given a `mention`,
its `sentence`, and a **closed Stage-2 candidate set**, emit a constrained JSON
verdict — `select` a candidate id, declare a `new_entity`, or `abstain` — under a
JSON schema whose `canonical_id` enum *is* the candidate set, then re-validate it
through the PALS layer ([`resolver_shared.to_resolution`](../../src/doc_ray/adapters/resolver_shared.py)).

This is **not** an open-ended generation or reasoning task. Two consequences drive
model choice:

1. **Bigger is not automatically better.** The work is disambiguation +
   constrained classification over a short context, called once per novel surface
   form (D-019) in the synchronous worker (D-005). A 70B model that is 4× slower
   for a marginal accuracy gain may be the wrong pick; latency is a first-class cost.
2. **The metric is not raw capability.** PALS already coerces hallucinated ids to
   ABSTAIN, so a "smart but reckless" model doesn't produce *wrong commits* — it
   produces **over-abstention**, which floods the human review queue. The right
   model minimizes confident-wrong links *first*, then review-queue load, then latency.

## Why there are no numbers checked in

A benchmark table is only worth anything if it was produced by running the real
models. The H200 has the GPU headroom (~140 GiB free) but, at authoring time,
**the Ollama container was not running (nothing on `:11434`) and no
`ANTHROPIC_API_KEY` was present**, so the numbers must be generated on the box. This directory ships the *harness* and the *gold set*;
you run it and commit the resulting `out/leaderboard.md`.

## Metrics (per model, over the gold set)

| metric | meaning | want |
|---|---|---|
| `verdict_accuracy` | fraction of cases whose final verdict matches gold (right kind, and right id for `select`) | high |
| `hard_error_rate` | **primary** — fraction that committed a *confident wrong link* (wrong id, or select/new where truth is abstain). The outcome PALS exists to prevent. | ~0 |
| `over_abstain_rate` | fraction that abstained where a real answer existed (soft miss → review-queue load) | low |
| `pals_coercions` | count of raw `select` verdicts PALS demoted to abstain (out-of-set / malformed id) — how hard the model fights the schema | low |
| `schema_invalid` | count of raw outputs that failed the verifier or weren't valid JSON — measures Ollama constrained-decoding reliability for that model | 0 |
| `latency_p50_s` / `p95_s` | wall-clock per resolve, warm model (warm-up call excluded) | low |

**Decision order:** lowest `hard_error_rate` → highest `verdict_accuracy` →
lowest `over_abstain_rate` → lowest latency. A model is only a viable Anthropic
replacement if `schema_invalid` is 0 (or near it): an unreliable structured-output
path is disqualifying regardless of accuracy.

## Candidate shortlist (H200, tool/JSON-capable)

All of these fit the H200 with room to spare and expose Ollama structured-output /
`format` support. Start with the mid-tier; only escalate to 70B if `hard_error_rate`
or `over_abstain_rate` is unacceptable. Sizes are the reason for the ordering, not a
capability claim — capability is what the benchmark measures.

| Ollama tag | ~params | why it's a candidate |
|---|---|---|
| `qwen2.5:7b-instruct` | 7B | current pinned default; fastest; strong structured-output track record — the floor to beat |
| `qwen2.5:32b-instruct` | 32B | the likely sweet spot: strong function-calling/structured output, still fast on an H200 |
| `mistral-small` | ~22–24B | alternative mid-tier family; independent failure modes from Qwen |
| `llama3.3:70b-instruct-q4_K_M` | 70B (Q4) | the "does more scale actually help *this* task" datapoint; slowest — justify with numbers |
| `qwen2.5:72b-instruct` | 72B | Qwen ceiling; run only if 32B shows a real accuracy gap that scale might close |

Model availability moves fast; confirm current tags against the Ollama library
before pulling. Structured-output / tool support for the Qwen2.5, Llama 3.1+, and
Mistral families is documented by Ollama
([structured outputs](https://docs.ollama.com/capabilities/structured-outputs)).

## Running it

Ollama runs as its **own container** from [`docker-compose.ollama.yml`](../../docker-compose.ollama.yml)
— nothing is installed on the host. For the H200, uncomment the CDI `deploy.resources`
reservation on the `ollama` service in that file first (needs `nvidia-container-toolkit`;
verify with `docker run --rm --device nvidia.com/gpu=all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi -L`).
Otherwise Ollama runs CPU-only and the latency numbers are meaningless.

```bash
# 1. bring up just the ollama service (loopback-published on 127.0.0.1:11434)
docker compose -f docker-compose.yml -f docker-compose.ollama.yml up -d ollama

# 2. pull every candidate INTO the container (the compose ollama-pull one-shot
#    only pulls the single DOCRAY_OLLAMA_MODEL; a benchmark needs them all)
for m in qwen2.5:7b-instruct qwen2.5:32b-instruct mistral-small llama3.3:70b-instruct-q4_K_M; do
  docker compose -f docker-compose.yml -f docker-compose.ollama.yml exec ollama ollama pull "$m"
done

# 3. run the benchmark from the host against the published loopback port
#    (adds Claude as the reference row iff ANTHROPIC_API_KEY is set)
.venv/bin/python scripts/bench/bench_resolver_backends.py \
  --models qwen2.5:7b-instruct qwen2.5:32b-instruct mistral-small llama3.3:70b-instruct-q4_K_M \
  --host http://localhost:11434 \
  --out scripts/bench/out

# 4. read $DOCRAY_WORK_DIR/findings/bench/<date>/leaderboard.md (never committed)
```

Prefer to run the harness *inside* the compose network instead of over the
published port? Point it at the service DNS name with `--host http://ollama:11434`
from a container on that network. Set `ANTHROPIC_API_KEY` before the run to score
`claude/claude-opus-4-8` as the reference row (the bar the local model must clear).
Point `DOCRAY_OLLAMA_MODEL` at the winner in `.env` and set
`DOCRAY_RESOLVER_BACKEND=ollama`.

## Extending the gold set

[`resolver_gold.json`](resolver_gold.json) holds 12 hand-labeled cases spanning
clear select, cross-type disambiguation, alias harvest, new-entity (incl. empty
candidates and upstream mistype), and four abstain traps (ambiguous initials, no
context, and plausible-but-undetermined bait). It is a **decision-quality probe,
not a scale test.** For volume, add a mode that samples real
`(mention, sentence, candidate)` triples from the corpus — but keep every added
case's expected verdict *objectively* determined by its sentence, or the accuracy
column becomes an opinion poll.
