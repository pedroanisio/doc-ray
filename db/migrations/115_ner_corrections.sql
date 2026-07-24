-- Block A (P0) — durable human corrections to ML-produced NER, migration 115.
--
-- entity_mention rows are ephemeral: ON DELETE CASCADE from annotations
-- (migration 010), so every reannotate deletes and rebuilds them. A human fix
-- written there would vanish on the next run. This table stores corrections
-- keyed by a STABLE span identity (document_id, char_start, char_end) — the
-- same natural key spans already use (migration 007) — so the annotate pipeline
-- can re-apply them onto each fresh set of ML mentions. See
-- doc_ray.domain.ner_corrections for the deterministic reconcile that consumes
-- these rows, and docs/proposals/entity-fabric-substrate-lens-projection.md for
-- why corrections belong to the substrate.
--
-- PALS's LAW: this is the verification layer PURPOSE.md mandates — "AI proposes,
-- verification decides". The correction records the reviewer and the original ML
-- value, so every fix is attributable and reversible.

CREATE TABLE IF NOT EXISTS entity_mention_correction (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id    uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    action         text NOT NULL
                   CHECK (action IN ('reclassify', 'reject', 'add', 'adjust_span')),
    -- Target span: what is being corrected (the stable key across re-annotation).
    char_start     int  NOT NULL,
    char_end       int  NOT NULL CHECK (char_end >= char_start),
    -- Payload, per action.
    entity_type    text,                       -- required for reclassify / add
    surface        text,
    new_char_start int,                        -- required for adjust_span
    new_char_end   int,
    -- Provenance (PALS's Law): the ML value at correction time + who/why.
    original_type  text,
    rationale      text,
    reviewer       uuid REFERENCES principals(id) ON DELETE SET NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    -- Per-action applicability, enforced in the database (not only the app).
    CONSTRAINT em_correction_reclassify_type
        CHECK (action <> 'reclassify' OR entity_type IS NOT NULL),
    CONSTRAINT em_correction_add_type
        CHECK (action <> 'add' OR entity_type IS NOT NULL),
    CONSTRAINT em_correction_adjust_span
        CHECK (action <> 'adjust_span'
               OR (new_char_start IS NOT NULL
                   AND new_char_end IS NOT NULL
                   AND new_char_end >= new_char_start))
);

-- The correction log is read back per document + span; the latest wins.
CREATE INDEX IF NOT EXISTS idx_em_correction_doc_span
    ON entity_mention_correction (document_id, char_start, char_end, created_at DESC);

-- Row-level security, mirroring the jobs/backups document-scoping (migration
-- 043) and FORCE RLS (migration 106): a correction is readable/writable exactly
-- when its document is. docray_readable_documents()/docray_writable_documents()
-- already fold in admin, owner, ACL, and org visibility.
ALTER TABLE entity_mention_correction ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_mention_correction FORCE ROW LEVEL SECURITY;

CREATE POLICY em_correction_read ON entity_mention_correction
    FOR SELECT
    USING (docray_is_admin()
           OR document_id IN (SELECT * FROM docray_readable_documents()));

CREATE POLICY em_correction_write ON entity_mention_correction
    FOR ALL
    USING (docray_is_admin()
           OR document_id IN (SELECT * FROM docray_writable_documents()))
    WITH CHECK (docray_is_admin()
                OR document_id IN (SELECT * FROM docray_writable_documents()));

-- The webapp reads corrections (docray_api), the API/worker curate them
-- (docray_client) and the batch pipeline re-applies them (docray_batch).
GRANT SELECT ON entity_mention_correction TO docray_api;
GRANT SELECT, INSERT, DELETE ON entity_mention_correction TO docray_client, docray_batch;

INSERT INTO schema_migrations (filename) VALUES ('115_ner_corrections.sql')
    ON CONFLICT (filename) DO NOTHING;
