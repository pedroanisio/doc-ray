-- doc-ray: drop the redundant duplicate index on spans(document_id, char_start, char_end).
--
-- 007_spans.sql created BOTH a UNIQUE constraint and a plain index on the exact
-- same columns, in the same order:
--
--   UNIQUE (document_id, char_start, char_end)  -> spans_document_id_char_start_char_end_key
--   CREATE INDEX idx_spans_document ON spans (document_id, char_start, char_end)
--
-- The plain index is 100% redundant: the unique constraint's backing index has an
-- identical column list and order, so it already serves every equality lookup,
-- range scan, ordering, and the document-delete FK cascade (leading column is
-- document_id, so 027_fk_cascade_indexes.sql's guarantee still holds). The planner
-- was merely splitting scans arbitrarily between two identical structures.
--
-- On the reference corpus idx_spans_document was ~1.7 GB (and grows with the
-- corpus). Dropping it reclaims that space and removes the redundant index
-- maintenance that every spans insert was paying, with no query-plan regression.
--
-- DROP INDEX (non-CONCURRENTLY) is transaction-safe and near-instant — a catalog
-- update plus file unlink — so the brief ACCESS EXCLUSIVE lock on spans is fine
-- inside the runner's per-file transaction.
DROP INDEX IF EXISTS idx_spans_document;

INSERT INTO schema_migrations (filename) VALUES ('078_drop_duplicate_spans_index.sql')
    ON CONFLICT (filename) DO NOTHING;
