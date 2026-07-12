-- doc-ray: document lifecycle state (BL-059).
--
-- documents.status remains the ingestion pipeline state
-- (pending/extracted/failed). lifecycle_state is the operator/user lifecycle
-- for reversible archive and soft-delete workflows.

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS lifecycle_state text NOT NULL DEFAULT 'active',
  ADD COLUMN IF NOT EXISTS archived_at timestamptz,
  ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'documents_lifecycle_state_check'
  ) THEN
    ALTER TABLE documents
      ADD CONSTRAINT documents_lifecycle_state_check
      CHECK (lifecycle_state IN ('active', 'archived', 'deleted'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'documents_lifecycle_timestamps_check'
  ) THEN
    ALTER TABLE documents
      ADD CONSTRAINT documents_lifecycle_timestamps_check
      CHECK (
        (lifecycle_state = 'active' AND archived_at IS NULL AND deleted_at IS NULL)
        OR (lifecycle_state = 'archived' AND archived_at IS NOT NULL AND deleted_at IS NULL)
        OR (lifecycle_state = 'deleted' AND deleted_at IS NOT NULL)
      );
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_documents_lifecycle_state
    ON documents (lifecycle_state, updated_at DESC);

ALTER TABLE document_metadata_history
    DROP CONSTRAINT IF EXISTS document_metadata_history_field_check;

ALTER TABLE document_metadata_history
    ADD CONSTRAINT document_metadata_history_field_check
    CHECK (field IN ('title', 'description', 'tags', 'lifecycle_state'));

INSERT INTO schema_migrations (filename) VALUES ('051_document_lifecycle.sql')
    ON CONFLICT (filename) DO NOTHING;
