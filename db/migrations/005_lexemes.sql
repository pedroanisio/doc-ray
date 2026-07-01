-- doc-ray: lexeme layer (4NF). See the model discussion for the FD analysis.
--   lexemes            = analyzer-independent identity (type layer)
--   annotations        = one analyzer run over a document (holds the analyzer)
--   lexeme_occurrences = span -> lexeme within a run (token layer)
-- Everything derivable (surface, forms, lemma/pos/lang on an occurrence) is a view.

-- Extend the provenance vocabulary with the linguistic step.
ALTER TYPE provenance_kind ADD VALUE IF NOT EXISTS 'annotated';

-- TYPE layer: global, cross-document lexeme identity.
CREATE TABLE IF NOT EXISTS lexemes (
    uid   text PRIMARY KEY,                     -- lx_en.v_3f9a21c0
    lang  text NOT NULL,
    pos   text NOT NULL,
    lemma text NOT NULL,                        -- normalized: NFC + casefold
    UNIQUE (lang, pos, lemma)                   -- the canonical identity
);
CREATE INDEX IF NOT EXISTS idx_lexemes_lang_pos ON lexemes (lang, pos);
CREATE INDEX IF NOT EXISTS idx_lexemes_lemma_trgm ON lexemes USING gin (lemma gin_trgm_ops);

-- ANNOTATION run: the analyzer is a property of the run, not the lexeme.
CREATE TABLE IF NOT EXISTS annotations (
    id          ulid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    analyzer    text NOT NULL,                  -- 'rule/en-approx' | 'spacy/…@ver'
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, analyzer)              -- one run per (document, analyzer)
);

-- TOKEN layer: a span IS a lexeme, within a run. Nothing derivable is stored.
CREATE TABLE IF NOT EXISTS lexeme_occurrences (
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    char_start    int  NOT NULL,
    char_end      int  NOT NULL,
    lexeme_uid    text NOT NULL REFERENCES lexemes(uid),
    PRIMARY KEY (annotation_id, char_start, char_end),
    CHECK (char_end > char_start)
);
CREATE INDEX IF NOT EXISTS idx_occ_lexeme ON lexeme_occurrences (lexeme_uid);

-- Derived views (the fields we deliberately did NOT store).
CREATE OR REPLACE VIEW lexeme_occurrence_detail AS
SELECT a.document_id, a.analyzer, o.annotation_id, o.char_start, o.char_end,
       o.lexeme_uid, l.lang, l.pos, l.lemma,
       substring(c.full_text FROM o.char_start + 1 FOR o.char_end - o.char_start) AS surface
FROM lexeme_occurrences o
JOIN annotations a      ON a.id = o.annotation_id
JOIN lexemes l          ON l.uid = o.lexeme_uid
JOIN document_content c ON c.document_id = a.document_id;

CREATE OR REPLACE VIEW lexeme_forms AS
SELECT DISTINCT lexeme_uid, surface FROM lexeme_occurrence_detail;

CREATE OR REPLACE VIEW lexeme_keys AS
SELECT uid, lang || ':' || pos || ':' || lemma AS canonical_key FROM lexemes;
