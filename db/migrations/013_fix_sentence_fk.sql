-- doc-ray: fix ON DELETE behavior for sentence back-references.
--
-- tokens.sentence_id and amr_node.sentence_id referenced sentences(id) with the
-- default NO ACTION, so deleting a document (which cascades to its sentences)
-- could fail. These links are advisory, so SET NULL is the right semantics —
-- the token/AMR node survives if a sentence is removed independently, and a
-- full document delete still cascades cleanly (the rows are also owned by their
-- annotation run).

ALTER TABLE tokens DROP CONSTRAINT IF EXISTS tokens_sentence_id_fkey;
ALTER TABLE tokens ADD CONSTRAINT tokens_sentence_id_fkey
    FOREIGN KEY (sentence_id) REFERENCES sentences(id) ON DELETE SET NULL;

ALTER TABLE amr_node DROP CONSTRAINT IF EXISTS amr_node_sentence_id_fkey;
ALTER TABLE amr_node ADD CONSTRAINT amr_node_sentence_id_fkey
    FOREIGN KEY (sentence_id) REFERENCES sentences(id) ON DELETE SET NULL;

INSERT INTO schema_migrations (filename) VALUES ('013_fix_sentence_fk.sql')
    ON CONFLICT (filename) DO NOTHING;
