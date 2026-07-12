-- doc-ray: persist page geometry for spans (BL-077).
--
-- The spans table remains the canonical address for text offsets. PDF layout
-- runs are stored per document so newly-created spans (sentences, tokens,
-- entities, chunks) can inherit page/bbox data after ingestion without
-- re-opening the original blob. Existing rows are intentionally left null;
-- BL-078 owns re-extraction/backfill.

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS page_number int,
    ADD COLUMN IF NOT EXISTS bbox_x0 double precision,
    ADD COLUMN IF NOT EXISTS bbox_y0 double precision,
    ADD COLUMN IF NOT EXISTS bbox_x1 double precision,
    ADD COLUMN IF NOT EXISTS bbox_y1 double precision;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'spans_page_geometry_complete'
    ) THEN
        ALTER TABLE spans ADD CONSTRAINT spans_page_geometry_complete CHECK (
            (
                page_number IS NULL
                AND bbox_x0 IS NULL AND bbox_y0 IS NULL
                AND bbox_x1 IS NULL AND bbox_y1 IS NULL
            )
            OR
            (
                page_number > 0
                AND bbox_x0 IS NOT NULL AND bbox_y0 IS NOT NULL
                AND bbox_x1 IS NOT NULL AND bbox_y1 IS NOT NULL
                AND bbox_x1 >= bbox_x0 AND bbox_y1 >= bbox_y0
            )
        ) NOT VALID;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_spans_document_page
    ON spans (document_id, page_number, char_start)
    WHERE page_number IS NOT NULL;

CREATE TABLE IF NOT EXISTS document_layout_runs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal     int  NOT NULL,
    page_number int  NOT NULL CHECK (page_number > 0),
    page_width  double precision,
    page_height double precision,
    char_start  int  NOT NULL,
    char_end    int  NOT NULL CHECK (char_end >= char_start),
    bbox_x0     double precision NOT NULL,
    bbox_y0     double precision NOT NULL,
    bbox_x1     double precision NOT NULL,
    bbox_y1     double precision NOT NULL,
    text        text NOT NULL DEFAULT '',
    UNIQUE (document_id, ordinal),
    CHECK (page_width IS NULL OR page_width > 0),
    CHECK (page_height IS NULL OR page_height > 0),
    CHECK (bbox_x1 >= bbox_x0),
    CHECK (bbox_y1 >= bbox_y0)
);

CREATE INDEX IF NOT EXISTS idx_document_layout_runs_document_range
    ON document_layout_runs (document_id, char_start, char_end);
CREATE INDEX IF NOT EXISTS idx_document_layout_runs_document_page
    ON document_layout_runs (document_id, page_number, ordinal);

SELECT docray_attach_rls('document_layout_runs'::regclass, 'document_id IN (%DOCS%)');
GRANT SELECT, INSERT, UPDATE, DELETE ON document_layout_runs TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('049_span_page_geometry.sql')
    ON CONFLICT (filename) DO NOTHING;
