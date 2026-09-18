-- doc-ray: index the FK columns the annotation delete-cascade depends on.
--
-- Failure mode: re-annotating a ~500k-character document wedged the
-- worker for 26+ minutes at 100% Postgres CPU. The annotator's replace-layer
-- DELETE FROM annotations cascades to tokens, and each deleted token forces a
-- lookup of referencing dependency rows — dependency(dependent_token_id) had
-- no usable index (only the composite (annotation_id, dependent_token_id),
-- whose leading column does not help), so every one of 76k token deletes
-- sequentially scanned the corpus-wide dependency table: quadratic. Same
-- class of gap on coref_chain(annotation_id) and resolution_review's FK.
--
-- Rule captured here: every FK on the annotations delete-cascade path carries
-- an index whose LEADING column is the FK column itself.

CREATE INDEX IF NOT EXISTS idx_dependency_dependent ON dependency (dependent_token_id);
CREATE INDEX IF NOT EXISTS idx_coref_chain_annotation ON coref_chain (annotation_id);
CREATE INDEX IF NOT EXISTS idx_resolution_review_mention ON resolution_review (mention_id);

INSERT INTO schema_migrations (filename) VALUES ('027_fk_cascade_indexes.sql')
    ON CONFLICT (filename) DO NOTHING;
