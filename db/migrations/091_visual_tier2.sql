-- doc-ray: Prism Tier-2 visual layer — vision embeddings for cross-modal retrieval.
--
-- Two additive, document-scoped, HNSW-indexed vector tables (proposal §5.2):
--   * image_embedding           — one vector per (image, model) for image<->image
--                                 and text->image search.
--   * sentence_vision_embedding — the REAL text-tower vector in the SAME space as
--                                 image_embedding. Required: the existing
--                                 `embedding` table is hash-BoW (non-semantic) and
--                                 not comparable to CLIP/SigLIP space, so text->image
--                                 search MUST query this table, never `embedding`.
--
-- The vector dimension is frozen here at 768 (CLIP/SigLIP ViT-L class), matching
-- the existing `embedding`/`canonical` vector columns; a re-embed under a new
-- model inserts a new (…, model) row and never overwrites. PALS posture: a
-- temperature-free encoder pass is reproducible given fixed weights/preprocessing
-- but is used ONLY for ranking — it never asserts a fact into the trusted graph.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS image_embedding (
    document_image_id    ulid NOT NULL REFERENCES document_image(id) ON DELETE CASCADE,
    document_id          uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,  -- denormalized for RLS
    model                text NOT NULL,
    vector               vector(768) NOT NULL,
    preprocessing_sha256 char(64) NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_image_id, model)
);

CREATE INDEX IF NOT EXISTS idx_image_embedding_hnsw
    ON image_embedding USING hnsw (vector vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_image_embedding_doc ON image_embedding (document_id);

SELECT docray_attach_rls('image_embedding'::regclass, 'document_id IN (%DOCS%)');
GRANT SELECT, INSERT, UPDATE, DELETE ON image_embedding TO docray_client;


CREATE TABLE IF NOT EXISTS sentence_vision_embedding (
    sentence_id  ulid NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,  -- denormalized for RLS
    model        text NOT NULL,
    vector       vector(768) NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sentence_id, model)
);

CREATE INDEX IF NOT EXISTS idx_sentence_vision_embedding_hnsw
    ON sentence_vision_embedding USING hnsw (vector vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_sentence_vision_embedding_doc
    ON sentence_vision_embedding (document_id);

SELECT docray_attach_rls('sentence_vision_embedding'::regclass, 'document_id IN (%DOCS%)');
GRANT SELECT, INSERT, UPDATE, DELETE ON sentence_vision_embedding TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('091_visual_tier2.sql')
    ON CONFLICT (filename) DO NOTHING;
