-- doc-ray: verified document metadata suggestions (BL-061).
--
-- Suggestions are review rows, never direct writes. The worker generates
-- deterministic title/tag/duplicate candidates, verifies each candidate through
-- the shared ML/LLM output gate, then stores pending rows for human review.

CREATE TABLE IF NOT EXISTS document_metadata_suggestion (
    id              ulid PRIMARY KEY,
    document_id     uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    field           text NOT NULL CHECK (field IN ('title', 'tags', 'duplicate')),
    current_value   text,
    suggested_value text NOT NULL CHECK (length(btrim(suggested_value)) > 0),
    confidence      real NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    reason          text NOT NULL CHECK (length(btrim(reason)) > 0),
    source          text NOT NULL DEFAULT 'verified-deterministic',
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    decided_at      timestamptz,
    UNIQUE (document_id, field, suggested_value)
);

CREATE INDEX IF NOT EXISTS idx_document_metadata_suggestion_doc
    ON document_metadata_suggestion (document_id, status, confidence DESC);

SELECT docray_attach_rls('document_metadata_suggestion'::regclass, 'document_id IN (%DOCS%)');
GRANT SELECT, UPDATE ON document_metadata_suggestion TO docray_client;

INSERT INTO schema_migrations (filename)
VALUES ('055_document_metadata_suggestions.sql')
ON CONFLICT (filename) DO NOTHING;
