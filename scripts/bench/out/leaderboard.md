---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "scripts/bench/bench_resolver_backends.py"
  date: "run-date-not-embedded (see JSON sidecar)"
---

# Resolver backend benchmark

Lower `hard_error_rate` is the primary criterion (a confident wrong link is the outcome PALS exists to prevent); then `verdict_accuracy`, then `over_abstain_rate` (review-queue load), then latency.

| model | cases | verdict_acc | hard_err | over_abstain | coercions | invalid | p50 s | p95 s |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| ollama/qwen2.5:7b-instruct | 12 | 0.667 | 0.167 | 0.167 | 0 | 0 | 0.61 | 0.69 |
| ollama/qwen2.5:14b-instruct | 12 | 0.833 | 0.0 | 0.167 | 0 | 0 | 0.73 | 0.83 |
| ollama/qwen2.5:32b-instruct | 12 | 0.917 | 0.083 | 0.0 | 0 | 0 | 1.17 | 1.62 |
| ollama/llama3.1:8b | 12 | 0.583 | 0.417 | 0.0 | 0 | 0 | 0.65 | 0.73 |
