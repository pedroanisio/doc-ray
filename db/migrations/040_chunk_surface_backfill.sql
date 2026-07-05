-- doc-ray: backfill chunk.surface from document_content.full_text.
--
-- See 039_entity_mention_surface_backfill.sql for the rationale (own
-- transaction, nested-loop/parallel-worker disabled to force a hash join over
-- document_content instead of a per-row re-decompress).

SET LOCAL max_parallel_workers_per_gather = 0;
SET LOCAL enable_nestloop = off;

UPDATE chunk ch
   SET surface = substring(c.full_text FROM sp.char_start + 1 FOR sp.char_end - sp.char_start)
  FROM annotations a, spans sp, document_content c
 WHERE a.id = ch.annotation_id
   AND sp.id = ch.span_id
   AND c.document_id = a.document_id
   AND ch.surface IS NULL;

INSERT INTO schema_migrations (filename) VALUES ('040_chunk_surface_backfill.sql')
    ON CONFLICT (filename) DO NOTHING;
