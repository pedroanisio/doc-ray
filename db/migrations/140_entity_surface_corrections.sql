-- Durable exact-surface NER correction rules (GitHub issue #41).
--
-- Span corrections remain keyed by (document_id, char_start, char_end). A
-- systematic rule has no honest span identity, so its span columns are NULL and
-- its key is the exact, case-sensitive (surface, surface_original_type) matcher.
-- The same append-only log replays both shapes chronologically; no sentinel or
-- precedence tier is introduced. Existing RLS, ownership, and grants are
-- unchanged because this extends the Block-A table rather than adding one.

ALTER TABLE entity_mention_correction
    ADD COLUMN IF NOT EXISTS surface_original_type text;

ALTER TABLE entity_mention_correction
    ALTER COLUMN char_start DROP NOT NULL,
    ALTER COLUMN char_end DROP NOT NULL;

ALTER TABLE entity_mention_correction
    DROP CONSTRAINT IF EXISTS entity_mention_correction_action_check;
ALTER TABLE entity_mention_correction
    ADD CONSTRAINT entity_mention_correction_action_check
    CHECK (action IN (
        'reclassify', 'reject', 'add', 'adjust_span',
        'reclassify_surface', 'reject_surface'
    ));

-- Preserve the old non-null span guarantee for all four existing actions, and
-- require honest NULL spans plus both matcher components for surface rules.
ALTER TABLE entity_mention_correction
    DROP CONSTRAINT IF EXISTS em_correction_scope_shape;
ALTER TABLE entity_mention_correction
    ADD CONSTRAINT em_correction_scope_shape CHECK (
        (
            action IN ('reclassify', 'reject', 'add', 'adjust_span')
            AND char_start IS NOT NULL
            AND char_end IS NOT NULL
        )
        OR
        (
            action IN ('reclassify_surface', 'reject_surface')
            AND char_start IS NULL
            AND char_end IS NULL
            AND surface IS NOT NULL
            AND btrim(surface) <> ''
            AND surface_original_type IS NOT NULL
            AND btrim(surface_original_type) <> ''
        )
    );

ALTER TABLE entity_mention_correction
    DROP CONSTRAINT IF EXISTS em_correction_reclassify_surface_type;
ALTER TABLE entity_mention_correction
    ADD CONSTRAINT em_correction_reclassify_surface_type CHECK (
        action <> 'reclassify_surface'
        OR (
            entity_type IS NOT NULL
            AND entity_type <> surface_original_type
        )
    );

COMMENT ON COLUMN entity_mention_correction.surface_original_type IS
    'Mandatory current-type matcher for exact surface rules; NULL for span actions.';

CREATE INDEX IF NOT EXISTS idx_em_correction_doc_surface
    ON entity_mention_correction
        (document_id, surface, surface_original_type, created_at, id)
    WHERE action IN ('reclassify_surface', 'reject_surface');

INSERT INTO schema_migrations (filename)
VALUES ('140_entity_surface_corrections.sql')
ON CONFLICT (filename) DO NOTHING;
