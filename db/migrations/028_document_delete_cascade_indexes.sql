-- doc-ray: index the FK columns the DOCUMENT delete-cascade depends on.
--
-- Live incident (2026-07-02): DELETE FROM documents on a fully annotated
-- 287-page book ran 24+ minutes at 100% CPU before being cancelled. The
-- cascade documents -> sentences -> tokens forced a sequential scan of the
-- corpus-wide tokens table (1.5M rows) for every one of the document's 4,576
-- sentences, because tokens(sentence_id) had no index; likewise each deleted
-- span sequentially scanned lexeme_occurrences (1.6M rows). With these
-- indexes in place the same delete completed in seconds.
--
-- Completes the rule stated in 027 for the annotations cascade, now applied
-- to the whole document cascade: every FK on any delete-cascade path carries
-- an index whose LEADING column is the FK column itself. Empty NLP-layer
-- tables (amr, srl, sentiment, timex, tlink, word_sense, discourse, event)
-- are included so they do not regress into the same trap once populated.

-- Hot today (measured row counts at incident time):
CREATE INDEX IF NOT EXISTS idx_tokens_sentence ON tokens (sentence_id);                -- 1.56M rows
CREATE INDEX IF NOT EXISTS idx_lexeme_occurrences_span ON lexeme_occurrences (span_id); -- 1.61M rows
CREATE INDEX IF NOT EXISTS idx_chunk_span ON chunk (span_id);                          -- 372k rows
CREATE INDEX IF NOT EXISTS idx_entity_mention_span ON entity_mention (span_id);
CREATE INDEX IF NOT EXISTS idx_coref_mention_span ON coref_mention (span_id);
CREATE INDEX IF NOT EXISTS idx_constituent_span ON constituent (span_id);
CREATE INDEX IF NOT EXISTS idx_documents_derived_from ON documents (derived_from);

-- Empty today, same trap once populated:
CREATE INDEX IF NOT EXISTS idx_amr_node_annotation ON amr_node (annotation_id);
CREATE INDEX IF NOT EXISTS idx_amr_node_sentence ON amr_node (sentence_id);
CREATE INDEX IF NOT EXISTS idx_discourse_relation_annotation ON discourse_relation (annotation_id);
CREATE INDEX IF NOT EXISTS idx_discourse_relation_arg1_span ON discourse_relation (arg1_span_id);
CREATE INDEX IF NOT EXISTS idx_discourse_relation_arg2_span ON discourse_relation (arg2_span_id);
CREATE INDEX IF NOT EXISTS idx_discourse_relation_connective_span ON discourse_relation (connective_span_id);
CREATE INDEX IF NOT EXISTS idx_event_annotation ON event (annotation_id);
CREATE INDEX IF NOT EXISTS idx_event_token ON event (token_id);
CREATE INDEX IF NOT EXISTS idx_sentiment_annotation ON sentiment (annotation_id);
CREATE INDEX IF NOT EXISTS idx_sentiment_span ON sentiment (span_id);
CREATE INDEX IF NOT EXISTS idx_sentiment_target_span ON sentiment (target_span_id);
CREATE INDEX IF NOT EXISTS idx_srl_argument_span ON srl_argument (span_id);
CREATE INDEX IF NOT EXISTS idx_srl_predicate_annotation ON srl_predicate (annotation_id);
CREATE INDEX IF NOT EXISTS idx_srl_predicate_token ON srl_predicate (token_id);
CREATE INDEX IF NOT EXISTS idx_timex_annotation ON timex (annotation_id);
CREATE INDEX IF NOT EXISTS idx_timex_span ON timex (span_id);
CREATE INDEX IF NOT EXISTS idx_tlink_annotation ON tlink (annotation_id);
CREATE INDEX IF NOT EXISTS idx_word_sense_annotation ON word_sense (annotation_id);

INSERT INTO schema_migrations (filename) VALUES ('028_document_delete_cascade_indexes.sql')
    ON CONFLICT (filename) DO NOTHING;
