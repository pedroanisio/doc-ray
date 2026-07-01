-- doc-ray: schema-review fixes (#3, #4, #6). All affected tables are empty
-- (tlink, word_sense, embedding — temporal/WSD/vector layers not yet populated),
-- so these are safe in-place restructures rather than data migrations.

-- --- #4: tlink gets real referential integrity ------------------------------
-- 011 modeled tlink.source_id / target_id as a *polymorphic* "event or timex id"
-- with no FK, so a deleted event/timex left dangling links. Replace each endpoint
-- with a typed nullable FK pair and a CHECK that exactly one side is set. Both
-- FKs cascade, so removing an anchor removes its links.
ALTER TABLE tlink DROP COLUMN IF EXISTS source_id;
ALTER TABLE tlink DROP COLUMN IF EXISTS target_id;
ALTER TABLE tlink ADD COLUMN IF NOT EXISTS source_event_id ulid REFERENCES event(id) ON DELETE CASCADE;
ALTER TABLE tlink ADD COLUMN IF NOT EXISTS source_timex_id ulid REFERENCES timex(id) ON DELETE CASCADE;
ALTER TABLE tlink ADD COLUMN IF NOT EXISTS target_event_id ulid REFERENCES event(id) ON DELETE CASCADE;
ALTER TABLE tlink ADD COLUMN IF NOT EXISTS target_timex_id ulid REFERENCES timex(id) ON DELETE CASCADE;
ALTER TABLE tlink DROP CONSTRAINT IF EXISTS tlink_source_one;
ALTER TABLE tlink ADD CONSTRAINT tlink_source_one
    CHECK (num_nonnulls(source_event_id, source_timex_id) = 1);
ALTER TABLE tlink DROP CONSTRAINT IF EXISTS tlink_target_one;
ALTER TABLE tlink ADD CONSTRAINT tlink_target_one
    CHECK (num_nonnulls(target_event_id, target_timex_id) = 1);
CREATE INDEX IF NOT EXISTS idx_tlink_source_event ON tlink (source_event_id);
CREATE INDEX IF NOT EXISTS idx_tlink_source_timex ON tlink (source_timex_id);
CREATE INDEX IF NOT EXISTS idx_tlink_target_event ON tlink (target_event_id);
CREATE INDEX IF NOT EXISTS idx_tlink_target_timex ON tlink (target_timex_id);

-- --- #6: word_sense primary key ---------------------------------------------
-- A token belongs to exactly one annotation run, so token_id already determines
-- annotation_id; the composite PK (token_id, annotation_id) was over-specified.
-- token_id alone is the candidate key. (annotation_id is kept as a column for
-- its cascade + querying.)
ALTER TABLE word_sense DROP CONSTRAINT IF EXISTS word_sense_pkey;
ALTER TABLE word_sense ADD PRIMARY KEY (token_id);

-- --- #6: documents.derived_from lineage should not block deletes -------------
-- NO ACTION meant deleting a source document was blocked while derivatives
-- referenced it. SET NULL lets the source go and severs the (advisory) link.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_derived_from_fkey;
ALTER TABLE documents ADD CONSTRAINT documents_derived_from_fkey
    FOREIGN KEY (derived_from) REFERENCES documents(id) ON DELETE SET NULL;

-- --- #3: document the token.upos <-> lexemes.pos invariant -------------------
-- upos is NOT redundant: ~23% of tokens (punctuation, symbols, spaces) carry a
-- upos with no lexeme. upos is the authoritative token POS; lexeme_uid is an
-- optional link to the type layer that must agree when present. Recorded in the
-- catalog so the intent travels with the schema.
COMMENT ON COLUMN tokens.upos IS
    'Universal POS — authoritative token-level POS. Present for every token, '
    'including lexeme-less ones (PUNCT/SYM/SPACE). When lexeme_uid is set, it '
    'equals lexemes.pos; the annotator maintains this agreement.';
COMMENT ON COLUMN tokens.lexeme_uid IS
    'Optional link to the analyzer-independent lexeme (type layer). NULL for '
    'tokens with no lemma/lexeme (punctuation, symbols, whitespace).';

-- --- #6: document the embedding fixed-dimension constraint -------------------
-- pgvector HNSW requires a fixed column dimension, so vector(768) is a real
-- constraint (default model = bge-base, 768-d), not an oversight. A model with a
-- different dimension needs its own table/index rather than mixing dims here.
COMMENT ON COLUMN embedding.vector IS
    'Fixed 768-d (bge-base default). pgvector HNSW requires a fixed dimension; '
    'a different-dimension model needs its own embedding table + HNSW index.';

INSERT INTO schema_migrations (filename) VALUES ('017_schema_review_fixes.sql')
    ON CONFLICT (filename) DO NOTHING;
