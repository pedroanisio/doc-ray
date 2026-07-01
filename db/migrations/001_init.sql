-- doc-ray initial schema
-- Runs automatically via /docker-entrypoint-initdb.d on first cluster init.

CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- fuzzy / substring metadata search

-- Core metadata table. Kept lean and hot: no large blobs live here.
CREATE TABLE IF NOT EXISTS documents (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title          text NOT NULL,
    doc_type       text NOT NULL,                       -- 'pdf', 'docx', ...
    author         text,
    source_uri     text NOT NULL,                       -- s3://bucket/key of original bytes
    content_sha256 char(64) NOT NULL UNIQUE,            -- dedupe + integrity
    byte_size      bigint NOT NULL,
    page_count     int,
    status         text NOT NULL DEFAULT 'pending',     -- pending|extracted|failed
    error          text,                                -- last failure reason, if any
    metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- flexible per-type fields
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Extracted full text kept separate so the metadata table stays small.
CREATE TABLE IF NOT EXISTS document_content (
    document_id  uuid PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    full_text    text,
    fts          tsvector GENERATED ALWAYS AS
                     (to_tsvector('english', coalesce(full_text, ''))) STORED
);

CREATE INDEX IF NOT EXISTS idx_documents_type_created
    ON documents (doc_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_status
    ON documents (status);
CREATE INDEX IF NOT EXISTS idx_documents_author_trgm
    ON documents USING gin (author gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_documents_metadata
    ON documents USING gin (metadata jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_content_fts
    ON document_content USING gin (fts);

-- Keep updated_at fresh.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_documents_updated_at ON documents;
CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
