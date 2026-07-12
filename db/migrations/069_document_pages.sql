-- doc-ray: authoritative per-page document geometry (BL-126).
--
-- document_layout_runs indexes text-bearing line geometry. It cannot represent
-- a text-free page without inventing a fake text run, so every consumer had to
-- guess whether an absent layout row meant "empty page", "unknown page", or
-- "not extracted". document_pages stores page dimensions and text presence for
-- every extracted page.

CREATE TABLE IF NOT EXISTS document_pages (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id    uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number    int  NOT NULL CHECK (page_number > 0),
    page_width     double precision NOT NULL CHECK (page_width > 0),
    page_height    double precision NOT NULL CHECK (page_height > 0),
    has_text       boolean NOT NULL DEFAULT false,
    text_run_count int NOT NULL DEFAULT 0 CHECK (text_run_count >= 0),
    UNIQUE (document_id, page_number),
    CHECK (has_text OR text_run_count = 0),
    CHECK (NOT has_text OR text_run_count > 0)
);

CREATE INDEX IF NOT EXISTS idx_document_pages_document_page
    ON document_pages (document_id, page_number);

SELECT docray_attach_rls('document_pages'::regclass, 'document_id IN (%DOCS%)');
GRANT SELECT, INSERT, UPDATE, DELETE ON document_pages TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('069_document_pages.sql')
    ON CONFLICT (filename) DO NOTHING;
