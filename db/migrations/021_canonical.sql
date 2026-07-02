-- doc-ray: canonical entity registry (entity-canonicalization layer, D-017).
-- One row per real-world entity; the remissive index (022_form_index) maps every
-- surface form to a row here. `id` is bigserial — this is a registry, not a
-- span/time-ordered artifact, so ULID identity is unnecessary (documented seam).
-- `embedding` (pgvector, from migration 012's extension) powers Stage-2 similarity
-- blocking; it stays NULL until the entity is embedded.

CREATE TABLE IF NOT EXISTS canonical (
    id           bigserial PRIMARY KEY,
    wikidata_id  text UNIQUE,                        -- 'Q188817'; NULL for local-only entities
    preferred    text NOT NULL,                      -- canonical display name
    entity_type  text NOT NULL,                      -- PERSON|PLACE|ORG|EVENT|...
    description  text,
    lang_primary text,
    embedding    vector(768),                        -- Stage-2 blocking; NULL until embedded
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_canonical_type ON canonical (entity_type);
CREATE INDEX IF NOT EXISTS idx_canonical_embedding_hnsw
    ON canonical USING hnsw (embedding vector_cosine_ops);

-- Keep updated_at fresh (reuse the trigger function from 001_init.sql).
DROP TRIGGER IF EXISTS trg_canonical_updated_at ON canonical;
CREATE TRIGGER trg_canonical_updated_at
    BEFORE UPDATE ON canonical
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

INSERT INTO schema_migrations (filename) VALUES ('021_canonical.sql')
    ON CONFLICT (filename) DO NOTHING;
