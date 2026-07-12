-- doc-ray: repair layout-run page dimensions for already-migrated databases.
--
-- Migration 049 creates document_layout_runs with page_width/page_height on
-- fresh databases. Some live databases applied 049 before those columns were
-- added to the migration file, so runtime ingest can fail when it persists
-- extracted page dimensions. This idempotent repair migration converges both
-- histories.

ALTER TABLE document_layout_runs
    ADD COLUMN IF NOT EXISTS page_width double precision,
    ADD COLUMN IF NOT EXISTS page_height double precision;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'document_layout_runs_page_width_check'
    ) THEN
        ALTER TABLE document_layout_runs
            ADD CONSTRAINT document_layout_runs_page_width_check
            CHECK (page_width IS NULL OR page_width > 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'document_layout_runs_page_height_check'
    ) THEN
        ALTER TABLE document_layout_runs
            ADD CONSTRAINT document_layout_runs_page_height_check
            CHECK (page_height IS NULL OR page_height > 0);
    END IF;
END $$;

INSERT INTO schema_migrations (filename)
VALUES ('054_document_layout_run_page_dimensions.sql')
ON CONFLICT (filename) DO NOTHING;
