-- doc-ray: shared span registry + layer-aware annotations (the annotation backbone).
--
-- Character offsets become a first-class, addressable entity (spans) so every
-- annotation layer references a span by FK instead of repeating offset triples.
-- sentences and lexeme_occurrences are refactored onto spans (offsets live ONLY
-- in spans now). annotations become layer-aware so a document can hold many
-- layers/tools without collision.

-- --- span registry ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS spans (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    char_start  int  NOT NULL,
    char_end    int  NOT NULL,
    UNIQUE (document_id, char_start, char_end),      -- offsets are the natural key
    CHECK (char_end >= char_start)
);
CREATE INDEX IF NOT EXISTS idx_spans_document ON spans (document_id, char_start, char_end);

-- Backfill spans from everything that currently carries offsets.
INSERT INTO spans (document_id, char_start, char_end)
    SELECT DISTINCT document_id, char_start, char_end FROM sentences
    ON CONFLICT DO NOTHING;
INSERT INTO spans (document_id, char_start, char_end)
    SELECT DISTINCT a.document_id, o.char_start, o.char_end
    FROM lexeme_occurrences o JOIN annotations a ON a.id = o.annotation_id
    ON CONFLICT DO NOTHING;

-- Views depend on occurrence offset columns; drop before altering.
DROP VIEW IF EXISTS lexeme_forms;
DROP VIEW IF EXISTS lexeme_occurrence_detail;

-- --- sentences -> span_id ----------------------------------------------------
ALTER TABLE sentences ADD COLUMN IF NOT EXISTS span_id uuid REFERENCES spans(id);
UPDATE sentences s SET span_id = sp.id
  FROM spans sp
 WHERE sp.document_id = s.document_id
   AND sp.char_start = s.char_start AND sp.char_end = s.char_end
   AND s.span_id IS NULL;
ALTER TABLE sentences ALTER COLUMN span_id SET NOT NULL;
ALTER TABLE sentences DROP COLUMN IF EXISTS char_start;
ALTER TABLE sentences DROP COLUMN IF EXISTS char_end;
CREATE INDEX IF NOT EXISTS idx_sentences_span ON sentences (span_id);

-- --- lexeme_occurrences -> span_id ------------------------------------------
ALTER TABLE lexeme_occurrences ADD COLUMN IF NOT EXISTS span_id uuid REFERENCES spans(id);
UPDATE lexeme_occurrences o SET span_id = sp.id
  FROM spans sp, annotations a
 WHERE a.id = o.annotation_id
   AND sp.document_id = a.document_id
   AND sp.char_start = o.char_start AND sp.char_end = o.char_end
   AND o.span_id IS NULL;
ALTER TABLE lexeme_occurrences ALTER COLUMN span_id SET NOT NULL;
ALTER TABLE lexeme_occurrences DROP CONSTRAINT IF EXISTS lexeme_occurrences_pkey;
ALTER TABLE lexeme_occurrences ADD PRIMARY KEY (annotation_id, span_id);
ALTER TABLE lexeme_occurrences DROP COLUMN IF EXISTS char_start;
ALTER TABLE lexeme_occurrences DROP COLUMN IF EXISTS char_end;

-- --- annotations -> layer-aware ---------------------------------------------
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS layer        text;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS tool         text;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS tool_version text;
UPDATE annotations SET layer = 'lexeme' WHERE layer IS NULL;
UPDATE annotations SET tool = analyzer  WHERE tool IS NULL AND analyzer IS NOT NULL;
UPDATE annotations SET tool = 'unknown' WHERE tool IS NULL;
ALTER TABLE annotations ALTER COLUMN layer SET NOT NULL;
ALTER TABLE annotations ALTER COLUMN tool  SET NOT NULL;
ALTER TABLE annotations DROP CONSTRAINT IF EXISTS annotations_document_id_analyzer_key;
ALTER TABLE annotations DROP COLUMN IF EXISTS analyzer;
ALTER TABLE annotations ADD CONSTRAINT annotations_doc_layer_tool_key
    UNIQUE (document_id, layer, tool);

-- --- recreate the lexeme views over spans -----------------------------------
CREATE VIEW lexeme_occurrence_detail AS
SELECT a.document_id, a.tool AS analyzer, o.annotation_id,
       sp.char_start, sp.char_end, o.lexeme_uid, l.lang, l.pos, l.lemma,
       substring(c.full_text FROM sp.char_start + 1 FOR sp.char_end - sp.char_start) AS surface
FROM lexeme_occurrences o
JOIN spans sp           ON sp.id = o.span_id
JOIN annotations a      ON a.id = o.annotation_id
JOIN lexemes l          ON l.uid = o.lexeme_uid
JOIN document_content c ON c.document_id = a.document_id;

CREATE VIEW lexeme_forms AS
SELECT DISTINCT lexeme_uid, surface FROM lexeme_occurrence_detail;

INSERT INTO schema_migrations (filename) VALUES ('007_spans.sql')
    ON CONFLICT (filename) DO NOTHING;
