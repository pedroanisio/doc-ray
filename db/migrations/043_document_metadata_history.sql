-- doc-ray: editable document metadata + change history (BL-058).
--
-- documents.title/tags were write-once at ingest: no mutation or route could
-- rename a document or edit its title, description, or tags afterward. This
-- adds an editable `description` column and an append-only change-history
-- table — distinct from document_provenance (pipeline/ingest events) and
-- resolution_review/canonical_merge (entity-resolution audit), which cover
-- different concerns entirely.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS description text;

CREATE TABLE IF NOT EXISTS document_metadata_history (
    id                 ulid PRIMARY KEY,
    document_id        uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    field              text NOT NULL CHECK (field IN ('title', 'description', 'tags')),
    old_value          text,
    new_value          text,
    changed_at         timestamptz NOT NULL DEFAULT now(),
    actor              text,                             -- caller-supplied label (user / service)
    actor_principal_id uuid REFERENCES principals(id) ON DELETE SET NULL
);

-- Reconstruct one document's edit history in event order (ULID sort == time order).
CREATE INDEX IF NOT EXISTS idx_metadata_history_doc
    ON document_metadata_history (document_id, id);

-- Same document-scoped read/write policy as document_provenance (018_rbac.sql).
SELECT docray_attach_rls('document_metadata_history'::regclass, 'document_id IN (%DOCS%)');
GRANT SELECT ON document_metadata_history TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('043_document_metadata_history.sql')
    ON CONFLICT (filename) DO NOTHING;
