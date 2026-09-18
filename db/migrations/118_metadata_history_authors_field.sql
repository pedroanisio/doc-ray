-- doc-ray: allow `authors` in the document metadata history.
--
-- SYMPTOM: every metadata edit that changed a
-- document's authors failed the whole update_metadata job with
--   new row for relation "document_metadata_history"
--   violates check constraint "document_metadata_history_field_check"
-- Every such job failed until this was traced.
--
-- ROOT CAUSE. `document_metadata_history` records one row per changed field,
-- gated by a CHECK listing the editable vocabulary. Migration 045 created it
-- (title/description/tags) and 053 widened it for lifecycle_state. The
-- authors-edit capability was later added to the *writer*
-- (adapters/persistence_doc_admin.update_document_metadata calls
-- _record("authors", ...)) and to the GraphQL/MCP surfaces, but no migration
-- widened the CHECK. Two sides of one contract drifted, and nothing bound them:
-- the DB-backed tests that cover author edits were red on main, so a genuine
-- defect hid among tolerated failures.
--
-- FIX. Admit 'authors'. The vocabulary now has a single source of truth in code
-- (persistence_doc_admin.HISTORY_FIELDS) and a drift guard
-- (tests/test_metadata_history_contract.py) that compares this constraint to it,
-- so the next editable field cannot ship half-migrated.

ALTER TABLE document_metadata_history
    DROP CONSTRAINT IF EXISTS document_metadata_history_field_check;

ALTER TABLE document_metadata_history
    ADD CONSTRAINT document_metadata_history_field_check
    CHECK (field IN ('title', 'description', 'tags', 'authors', 'lifecycle_state'));

INSERT INTO schema_migrations (filename)
VALUES ('118_metadata_history_authors_field.sql')
ON CONFLICT (filename) DO NOTHING;
