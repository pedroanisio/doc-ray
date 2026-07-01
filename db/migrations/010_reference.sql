-- doc-ray: entities & reference (NLP layers 11, 12, 13).

-- 11 · named entity recognition (mention span + type)
CREATE TABLE IF NOT EXISTS entity_mention (
    id            ulid PRIMARY KEY,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    span_id       uuid NOT NULL REFERENCES spans(id),
    entity_type   text NOT NULL                    -- PER / ORG / GPE / DATE / ...
);
CREATE INDEX IF NOT EXISTS idx_entity_mention_annotation ON entity_mention (annotation_id);

-- 12 · entity linking (mention -> KB id; NIL = absence of a row)
CREATE TABLE IF NOT EXISTS entity_link (
    mention_id ulid NOT NULL REFERENCES entity_mention(id) ON DELETE CASCADE,
    scheme     text NOT NULL,                       -- 'wikidata' | 'dbpedia' | ...
    kb_id      text NOT NULL,                        -- 'Q64'
    confidence real,
    PRIMARY KEY (mention_id, scheme)
);

-- 13 · coreference (chain 1:N mention)
CREATE TABLE IF NOT EXISTS coref_chain (
    id            ulid PRIMARY KEY,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    document_id   uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS coref_mention (
    chain_id ulid NOT NULL REFERENCES coref_chain(id) ON DELETE CASCADE,
    span_id  uuid NOT NULL REFERENCES spans(id),
    PRIMARY KEY (chain_id, span_id)
);
CREATE INDEX IF NOT EXISTS idx_coref_chain_document ON coref_chain (document_id);

INSERT INTO schema_migrations (filename) VALUES ('010_reference.sql')
    ON CONFLICT (filename) DO NOTHING;
