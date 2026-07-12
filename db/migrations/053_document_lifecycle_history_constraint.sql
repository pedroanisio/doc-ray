-- doc-ray: allow lifecycle transitions in document metadata history.
--
-- Migration 051 creates the lifecycle columns and uses
-- document_metadata_history as the operator-facing audit trail. Databases that
-- had already applied 051 before this constraint widening need this repair
-- migration so soft-delete/archive/restore can record lifecycle_state changes.

ALTER TABLE document_metadata_history
    DROP CONSTRAINT IF EXISTS document_metadata_history_field_check;

ALTER TABLE document_metadata_history
    ADD CONSTRAINT document_metadata_history_field_check
    CHECK (field IN ('title', 'description', 'tags', 'lifecycle_state'));

INSERT INTO schema_migrations (filename)
VALUES ('053_document_lifecycle_history_constraint.sql')
ON CONFLICT (filename) DO NOTHING;
