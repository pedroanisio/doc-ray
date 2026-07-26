---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-14"
retire-when: >-
  No document carries the pending-trf-h200 tag any more — i.e. the deferred
  oversized documents have been re-annotated with en_core_web_trf on an H200
  (or the operator abandons that plan). Delete this file in the same change.
---

# Pending trf re-annotation (H200)

English annotation now defaults to the transformer model `en_core_web_trf`
(fr/pt stay on the `*_core_news_sm` CNN models). The corpus was re-annotated on
the local **8 GB RTX 3050** dev GPU, which reliably runs `en_core_web_trf` only
for documents up to **~120k tokens**. Larger documents exceed 8 GB of VRAM
(peak scales with document length; the pipeline holds the previous document's
transformer activations while processing the next, so a large book needs ~2×
its footprint) and OOM.

Per operator decision, the oversized documents are **deferred to an H200** run
rather than chunked or downgraded. They carry the tag **`pending-trf-h200`**.

> **Current state:** these documents are **unannotated** (their prior
> `en_core_web_sm` runs were removed during the migration and the worker image
> no longer ships `en_core_web_sm`). They stay unannotated until the H200 run.

## Work-list

```sql
-- every document awaiting trf on the H200
SELECT d.id,
       (length(dc.full_text) / 4.85)::int AS est_tokens,
       d.title
FROM documents d
JOIN document_content dc ON dc.document_id = d.id
JOIN document_tags dt ON dt.document_id = d.id
JOIN tags t ON t.id = dt.tag_id
WHERE t.tag = 'pending-trf-h200'
ORDER BY est_tokens DESC;
```

## Running it on the H200

The worker image and code are already H200-ready — the 8 GB limits below are the
*only* reason this is deferred. On a 141 GB H200 no threshold is needed and
several workers fit.

1. Build/pull the worker image (unchanged): `docker compose build worker`.
2. Bring the worker up with the GPU overlay. On an H200 the `expandable_segments`
   setting is harmless and you can raise the replica count:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.gpu.yml \
     up -d --scale worker=4 worker
   ```

3. Enqueue an `annotate` job per tagged document (delete any lingering
   `en_core_web_sm` run first — there should be none):

   ```python
   import psycopg
   from doc_ray.adapters import queue_postgres as q
   from doc_ray.config import Config

   with psycopg.connect(Config.from_env().database_url) as conn:
       ids = [r[0] for r in conn.execute(
           "SELECT dt.document_id FROM document_tags dt "
           "JOIN tags t ON t.id = dt.tag_id WHERE t.tag = 'pending-trf-h200'"
       ).fetchall()]
       for doc_id in ids:
           conn.execute(
               "DELETE FROM annotations WHERE document_id=%s "
               "AND layer IN ('spacy','lexeme') AND tool LIKE '%%en_core_web_sm%%'",
               (doc_id,),
           )
           q.enqueue(conn, "annotate", {"lang": "en"}, document_id=doc_id)
           conn.commit()
   ```

4. After the queue drains, drop the tag from the documents that now carry a trf
   run:

   ```sql
   DELETE FROM document_tags dt USING tags t, annotations a
   WHERE dt.tag_id = t.id AND t.tag = 'pending-trf-h200'
     AND a.document_id = dt.document_id
     AND a.layer = 'spacy' AND a.tool LIKE '%trf%';
   ```

5. Confirm none remain:

   ```sql
   SELECT count(*) FROM document_tags dt JOIN tags t ON t.id = dt.tag_id
   WHERE t.tag = 'pending-trf-h200';   -- expect 0
   ```

## Why the local card is capped at ~120k tokens

Measured on the RTX 3050 (8 GB), single GPU worker, `mixed_precision` on:

| Document size    | Result                                  |
|------------------|-----------------------------------------|
| ≤ 97k tokens     | reliable (peak ~6.3 GB), chains cleanly |
| ~130–145k tokens | flaky (2/5 succeeded, peak ~7.8 GB)     |
| ≥ 238k tokens    | OOM (needs > 8 GB)                       |

The worker runs a single GPU replica (two `en_core_web_trf` workers exhaust an
8 GB card), with `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` and a
per-job GPU-memory release between documents (`annotation_spacy._release_gpu_memory`).
None of these lift the per-document ceiling — only more VRAM does, hence the H200.
