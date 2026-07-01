-- doc-ray: shallow + deep syntax (NLP layers 8, 9, 10).

-- 8 · chunking (BIO collapsed to spans: NP/VP/PP)
CREATE TABLE IF NOT EXISTS chunk (
    id            ulid PRIMARY KEY,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    span_id       uuid NOT NULL REFERENCES spans(id),
    chunk_type    text NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_annotation ON chunk (annotation_id);

-- 9 · constituency (phrase-structure tree, adjacency list)
CREATE TABLE IF NOT EXISTS constituent (
    id            ulid PRIMARY KEY,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    parent_id     ulid REFERENCES constituent(id) ON DELETE CASCADE,
    label         text NOT NULL,
    span_id       uuid REFERENCES spans(id)
);
CREATE INDEX IF NOT EXISTS idx_constituent_parent ON constituent (parent_id);
CREATE INDEX IF NOT EXISTS idx_constituent_annotation ON constituent (annotation_id);

-- 10 · dependency (each token has exactly one head -> a function)
CREATE TABLE IF NOT EXISTS dependency (
    annotation_id      ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    dependent_token_id ulid NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    head_token_id      ulid REFERENCES tokens(id),          -- NULL = root
    deprel             text NOT NULL,
    PRIMARY KEY (annotation_id, dependent_token_id)
);
CREATE INDEX IF NOT EXISTS idx_dependency_head ON dependency (head_token_id);

INSERT INTO schema_migrations (filename) VALUES ('009_syntax.sql')
    ON CONFLICT (filename) DO NOTHING;
