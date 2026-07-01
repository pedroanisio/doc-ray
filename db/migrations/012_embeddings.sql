-- doc-ray: distributed representations / embeddings (NLP layer 20).
-- Requires the pgvector extension (image: pgvector/pgvector:pg16).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS embedding (
    span_id uuid       NOT NULL REFERENCES spans(id) ON DELETE CASCADE,
    model   text       NOT NULL,                  -- e.g. 'bge-base-en@1.5'
    vector  vector(768) NOT NULL,
    PRIMARY KEY (span_id, model)
);

-- Approximate nearest-neighbor search by cosine distance.
CREATE INDEX IF NOT EXISTS idx_embedding_hnsw
    ON embedding USING hnsw (vector vector_cosine_ops);

INSERT INTO schema_migrations (filename) VALUES ('012_embeddings.sql')
    ON CONFLICT (filename) DO NOTHING;
