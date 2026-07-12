-- doc-ray: document collections (BL-057).
--
-- Manual collections keep explicit membership rows. Smart collections keep a
-- JSON rule (currently doc_type/status/tags) and resolve matching documents at
-- read time, so new matching documents appear without backfill.

CREATE TABLE IF NOT EXISTS document_collections (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL UNIQUE,
    description text NOT NULL DEFAULT '',
    kind        text NOT NULL CHECK (kind IN ('manual', 'smart')),
    rule        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (kind = 'smart' OR rule = '{}'::jsonb)
);

DROP TRIGGER IF EXISTS trg_document_collections_updated_at ON document_collections;
CREATE TRIGGER trg_document_collections_updated_at
    BEFORE UPDATE ON document_collections
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS document_collection_members (
    collection_id uuid NOT NULL REFERENCES document_collections(id) ON DELETE CASCADE,
    document_id   uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    added_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_collection_members_document
    ON document_collection_members (document_id, collection_id);
CREATE INDEX IF NOT EXISTS idx_collection_members_collection_added
    ON document_collection_members (collection_id, added_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_collections_kind
    ON document_collections (kind, name);
CREATE INDEX IF NOT EXISTS idx_document_collections_rule
    ON document_collections USING gin (rule jsonb_path_ops);

-- Collection definitions are shared navigation metadata; membership rows are
-- document-scoped and inherit read/write policy from the document they include.
GRANT SELECT, INSERT, UPDATE, DELETE ON document_collections TO docray_client;
SELECT docray_attach_rls(
    'document_collection_members'::regclass,
    'document_id IN (%DOCS%)'
);
GRANT SELECT, INSERT, UPDATE, DELETE ON document_collection_members TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('047_document_collections.sql')
    ON CONFLICT (filename) DO NOTHING;
