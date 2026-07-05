-- doc-ray: backfill entity_mention.surface from document_content.full_text.
--
-- Runs alone (own transaction, RowExclusiveLock only — no AccessExclusiveLock,
-- so concurrent readers/writers of entity_mention are not blocked). Parallel
-- workers and nested-loop joins are disabled for this statement: a nested-loop
-- plan would re-fetch/decompress each document's (possibly multi-MB) TOASTed
-- full_text once per mention row instead of once per document, which is what
-- made the original combined migration run for hours. Disabling nestloop
-- steers the planner toward a hash join that builds document_content's small
-- (~one row per document) side once.

SET LOCAL max_parallel_workers_per_gather = 0;
SET LOCAL enable_nestloop = off;

UPDATE entity_mention em
   SET surface = substring(c.full_text FROM sp.char_start + 1 FOR sp.char_end - sp.char_start)
  FROM annotations a, spans sp, document_content c
 WHERE a.id = em.annotation_id
   AND sp.id = em.span_id
   AND c.document_id = a.document_id
   AND em.surface IS NULL;

INSERT INTO schema_migrations (filename) VALUES ('039_entity_mention_surface_backfill.sql')
    ON CONFLICT (filename) DO NOTHING;
