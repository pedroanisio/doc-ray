-- doc-ray: token layer + morphology + subwords (NLP layers 3, 4, 6, 7).
-- The token is the addressable unit that syntax/morphology reference. UD FEATS
-- and subword pieces decompose into child relations (no pipe-strings, no arrays).

CREATE TABLE IF NOT EXISTS tokens (
    id            ulid PRIMARY KEY,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    span_id       uuid NOT NULL REFERENCES spans(id),
    sentence_id   ulid REFERENCES sentences(id),
    ordinal       int  NOT NULL,                 -- token index within its sentence
    lexeme_uid    text REFERENCES lexemes(uid),  -- lemma + UPOS via the lexeme
    upos          text,                          -- Universal POS
    xpos          text,                          -- language-specific tag
    UNIQUE (annotation_id, span_id)
);
CREATE INDEX IF NOT EXISTS idx_tokens_annotation ON tokens (annotation_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_tokens_span       ON tokens (span_id);
CREATE INDEX IF NOT EXISTS idx_tokens_lexeme     ON tokens (lexeme_uid);

CREATE TABLE IF NOT EXISTS token_feature (       -- UD FEATS: (token, key) -> value
    token_id ulid NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    feature  text NOT NULL,
    value    text NOT NULL,
    PRIMARY KEY (token_id, feature)
);

CREATE TABLE IF NOT EXISTS subword (             -- BPE/WordPiece pieces, ordered
    token_id ulid NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    position int  NOT NULL,
    piece    text NOT NULL,
    PRIMARY KEY (token_id, position)
);

INSERT INTO schema_migrations (filename) VALUES ('008_tokens_morphology.sql')
    ON CONFLICT (filename) DO NOTHING;
