-- genre classification (issue #6) — genre / type classification, migration 127.
--
-- documents.doc_type records the FILE FORMAT (pdf, epub), not the GENRE. The
-- library holds fiction and non-fiction together, and they demand opposite
-- identity rules: a novel's characters are local to the work, a history's places
-- are the same Paris across every history. This table gives every document a
-- genre axis, which the per-entity-type resolution policy
-- (doc_ray.domain.genre_policy) reads to decide LOCAL vs GLOBAL resolution. This
-- block DEFINES the policy input; the projection engine (issue #8) applies it — nothing resolves
-- differently yet. See docs/design/entity-fabric.md.
--
-- PALS's LAW: a machine genre is an unverified guess. The classifier job writes
-- status='pending'; only a human confirmation (method='human') asserts a genre.
-- A wrong genre would later cause a wrong, hard-to-reverse identity merge, so low
-- confidence abstains into review rather than silently mislabelling a document.

CREATE TABLE IF NOT EXISTS document_classification (
    document_id  uuid PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    genre        text NOT NULL
                 CHECK (genre IN ('fiction', 'non_fiction', 'mixed', 'unknown')),
    subtype      text,                      -- e.g. 'novel', 'biography', 'manual', 'paper'
    confidence   real CHECK (confidence >= 0 AND confidence <= 1),
    method       text NOT NULL,             -- 'heuristic' | 'model:<name>' | 'human'
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'confirmed', 'rejected')),
    reviewer     uuid REFERENCES principals(id) ON DELETE SET NULL,
    rationale    text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Row-level security, mirroring the NER-correction table (migration 115) and
-- FORCE RLS (migration 106): a classification is readable/writable exactly when
-- its document is. docray_readable_documents()/docray_writable_documents() already
-- fold in admin, owner, ACL, and org visibility.
ALTER TABLE document_classification ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_classification FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS document_classification_read ON document_classification;
CREATE POLICY document_classification_read ON document_classification
    FOR SELECT
    USING (docray_is_admin()
           OR document_id IN (SELECT * FROM docray_readable_documents()));

DROP POLICY IF EXISTS document_classification_write ON document_classification;
CREATE POLICY document_classification_write ON document_classification
    FOR ALL
    USING (docray_is_admin()
           OR document_id IN (SELECT * FROM docray_writable_documents()))
    WITH CHECK (docray_is_admin()
                OR document_id IN (SELECT * FROM docray_writable_documents()));

-- The webapp/API read the classification (docray_api); the API records human
-- confirmations (docray_client) and the classifier job writes machine guesses
-- (docray_batch). Human confirmation and re-runs are UPDATEs, so both need it.
GRANT SELECT ON document_classification TO docray_api;
GRANT SELECT, INSERT, UPDATE ON document_classification TO docray_client, docray_batch;

INSERT INTO schema_migrations (filename) VALUES ('127_document_classification.sql')
    ON CONFLICT (filename) DO NOTHING;
