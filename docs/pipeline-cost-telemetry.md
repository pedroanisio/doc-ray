---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "scripts/cost_telemetry.py (measured from the live jobs table)"
  date: "2026-07-25"
---

# doc-ray — Pipeline Cost Telemetry

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Measured corpus

- Documents: **354**
- Sentences: **1,469,056** (4,150 per document)
- Source bytes: **5.05 GB** (14.6 MB per document)
- Database size: **54.11 GB** (156.5 MB per document, 38.6 KB per sentence)
- Measurement window: last 12 h of completed job runs

## Per-stage cost model

Fitted as `seconds = fixed + per_sentence x sentences` over successful runs. A low correlation means the fixed term dominates and the stage is size-insensitive.

| stage | lane | runs | fixed (s) | s/sentence | r | p50 (s) | p90 (s) | observed h |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `annotate` | gpu | 174 | 4.1 | 0.01820 | 0.91 | 63.4 | 164.4 | 3.94 |
| `semantic_embed` | gpu | 62 | 1.0 | 0.00606 | 0.94 | 39.5 | 101.6 | 0.86 |
| `canonicalize` | cpu | 174 | -0.7 | 0.00310 | 0.90 | 7.5 | 30.2 | 0.60 |
| `visual_extract` | gpu | 28 | 10.0 | 0.00949 | 0.33 | 0.0 | 126.3 | 0.40 |
| `semantic_enrich` | cpu | 174 | -1.4 | 0.00164 | 0.44 | 2.5 | 7.2 | 0.27 |
| `visual_embed` | gpu | 27 | 13.1 | 0.00522 | 0.58 | 28.0 | 43.7 | 0.21 |
| `ingest` | cpu | 18 | 17.1 | -0.00046 | -0.14 | 21.4 | 27.1 | 0.08 |
| `update_metadata` | cpu | 46 | 0.0 | 0.00000 | 0.43 | 0.1 | 0.1 | 0.00 |
| `add_document_tags` | cpu | 43 | 0.0 | -0.00000 | -0.49 | 0.0 | 0.0 | 0.00 |
| `suggest_document_metadata` | cpu | 6 | 0.0 | 0.00001 | 0.51 | 0.0 | 0.1 | 0.00 |

## Projected resource draw

Storage priced over **12 month(s)**; compute is a one-off per document ingested.

| documents | sentences | GPU h | CPU h | DB GB | object GB | cost |
|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 4,149,876 | 52.77 | 9.12 | 152.9 | 14.3 | — * |
| 10,000 | 41,498,757 | 527.66 | 91.19 | 1,528.5 | 142.8 | — * |
| 100,000 | 414,987,571 | 5,276.58 | 911.89 | 15,285.4 | 1,427.5 | — * |

\* Incomplete total — no rate supplied for `cpu_compute`, `db_storage`, `gpu_compute`, `object_storage`. Unpriced lines are excluded from the total rather than counted as zero; set them in `config/cost_rates.yaml` for a complete figure.

## Unit prices used

| rate | value (USD) | source |
|---|---:|---|
| `gpu_host_hourly` | **unset** | — |
| `cpu_host_hourly` | **unset** | — |
| `db_gb_month` | **unset** | — |
| `object_gb_month` | **unset** | — |

## What drives the cost

- **Sentences, not bytes.** Stage duration tracks sentence count; source file size is a weak proxy (a large scanned PDF yields little text, a dense monograph yields a lot).
- **The GPU lane is the throughput ceiling.** Each worker pool holds one job at a time, so GPU hours — dominated by `annotate` — set wall-clock time to process a batch. CPU-lane stages overlap with it and are effectively free in wall-clock terms.
- **Storage is recurring, compute is not.** Compute is paid once per document; the database and object store are paid every month the corpus exists, and the database is the larger of the two by an order of magnitude.
