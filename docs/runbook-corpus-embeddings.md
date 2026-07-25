---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-25"
---

# Corpus Embedding Backfill Runbook

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Use this runbook after a sentence-encoder cutover or whenever active-model
coverage must be restored. The operation enqueues jobs; workers perform the
embedding asynchronously.

## 1. Take the rollback backup

Do not enqueue a corpus-wide re-embed until a restorable backup exists. Use the
existing webapp **Backups** surface when `DOCRAY_BACKUP_ENABLED=true`, or take
the managed PostgreSQL backup described in
[`production-setup.md`](production-setup.md). Record the backup identifier and
confirm it is downloadable before continuing.

## 2. Record the current sentence and embedding counts

Run these queries against the target database:

```sql
SELECT count(*) AS sentence_count FROM sentences;
SELECT model, count(*) AS embedding_count
FROM embedding
GROUP BY model
ORDER BY model;
```

## 3. Enqueue the learned sentence backfill

From the repository root with `DATABASE_URL` set:

```bash
python scripts/enqueue_semantic_backfill.py --only-missing
```

The command prints `semantic_embed: enqueued N job(s)`. Re-running it after
the worker has embedded every eligible document should enqueue zero jobs. Use
`--all` only when every sentence-bearing document must be recomputed.

## 4. Wait for the worker queue to drain

Track the jobs through the webapp Jobs surface or GraphQL:

```graphql
query {
  jobs(kind: "semantic_embed", limit: 100) {
    id status documentId error
  }
}
```

Do not treat enqueue success as completion. Continue only when the relevant
jobs are terminal and none failed.

## 5. Verify semantic coverage and retrieval

Repeat the counts from step 2. For every sentence-bearing document, confirm an
active-model vector exists, then smoke-test a known corpus phrase:

```graphql
query SemanticSmoke($query: String!) {
  semanticSearchSentences(query: $query, limit: 5) {
    documentId ordinal text similarity
  }
}
```

Record the before/after counts and at least one ranked hit as deployment
evidence. An empty hit list is not success; verify worker failures, model tags,
and caller visibility before concluding the corpus has no match.

## 6. Verify visual-layer coverage

Measure the Tier-1 and Tier-3 populations without changing data:

```bash
python scripts/prism_live_stats.py --once
```

If extracted PDFs lack Tier-1 rows, enqueue the CPU-safe extraction backfill:

```bash
python scripts/enqueue_visual_backfill.py --tier extract
python scripts/prism_live_stats.py --once
```

Record both outputs. Tier-2 embedding and Tier-3 caption work require their
configured model workers; do not infer coverage from queued jobs alone.

For text-to-image search, enqueue missing Tier-2 vectors after confirming the
vision worker can access its configured device:

```bash
python scripts/enqueue_visual_backfill.py --tier embed
```

The worker sends at most `DOCRAY_VISION_BATCH_SIZE` images or sentence texts to
the dual encoder per call (default `16`) while retaining one atomic
delete-then-COPY write per Document. On a memory-constrained GPU, lower the
positive integer and restart the worker before retrying failed `visual_embed`
jobs. The default `WORKER_FAST_EXCLUDE_KINDS` includes `visual_embed`, keeping
these jobs on the GPU-capable heavy lane. Its default wall-clock limit is four
hours; override `WORKER_JOB_TIMEOUT_VISUAL_EMBED_SECONDS` before starting the
worker when a larger corpus needs a longer per-Document budget. Continue only
when the jobs are terminal and `image_embedding` is non-zero; an enqueued or
running job is not coverage evidence.

## Roll back

Stop or drain workers before restoring. Restore the backup recorded in step 1
using the same existing Backups surface or managed-database procedure used to
create it. After restoration, repeat the count and semantic-search checks to
prove the target state was recovered.
