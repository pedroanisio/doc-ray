-- doc-ray: schema SemVer 1.42.0 — NLP Layer 20 hard cutover (hash-BoW → learned).
--
-- Replaces the non-learned `hash-bow-768` sentence embedding with a learned
-- sentence-transformers encoder (see application/semantic_embed.py + the
-- `semantic_embed` job). Hard cutover / replacement: the old and new never serve
-- production traffic together, and the old vectors are DISCARDED, not kept.
--
-- No structural DDL is needed:
--   * the `embedding` table stays `vector(768)` — the recommended default model
--     (sentence-transformers/all-mpnet-base-v2) is 768-dim, so no column change;
--   * `docray_client` already holds SELECT and the table already carries the
--     span->document RLS policy (p_read), so the reader is grant-ready.
--
-- This migration performs the LEGACY PURGE: it deletes the discarded hash-BoW
-- rows so the only vectors in `embedding` are the learned ones. The reader
-- filters by the active model id, so a residual row could never be served
-- anyway; the purge removes dead weight. Idempotent — a no-op on a fresh DB and
-- on re-run. The corpus RE-EMBED is a separate operational step: enqueue the
-- `semantic_embed` job per document (workers with the sentence stack installed);
-- take a corpus backup first, which is the rollback path for the cutover.

DELETE FROM embedding WHERE model = 'hash-bow-768';

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.42.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('124_semantic_embedding_cutover.sql')
ON CONFLICT (filename) DO NOTHING;
