-- #15 (broader sweep) — resolution_review was invisible to every client caller.
--
-- resolution_review (migration 023) was created AFTER both the 018_rbac grant loop
-- and the 018/106 RLS rollout, so it had neither a docray_client SELECT grant nor
-- any RLS policy. The client-facing `resolutionReview` query
-- (canonical_read.list_resolution_review) therefore raised
-- "permission denied for table resolution_review" for every real caller — the same
-- class of bug migration 117 fixed for form_index/entity_profiles.
--
-- Unlike the global form_index (117), resolution_review is PER-DOCUMENT: a row ties
-- to a document through mention_id -> entity_mention -> annotations.document_id. A
-- bare grant would expose every tenant's review queue, so this scopes reads with
-- RLS as well as granting them — the classification the issue asked us to confirm
-- before landing. The batch writer (application/resolution.py) runs as docray_batch
-- (BYPASSRLS, migration 106), so FORCE RLS does not affect the write path.
--
-- Follows db/migrations/115_ner_corrections.sql; the predicate mirrors
-- entity_mention's own policy (migration 018/102) one join deeper, and terminates
-- in docray_readable_documents() / docray_writable_documents() (BYPASSRLS
-- SECURITY DEFINER, migration 114) so there is no policy recursion.

ALTER TABLE resolution_review ENABLE ROW LEVEL SECURITY;
ALTER TABLE resolution_review FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS resolution_review_read ON resolution_review;
CREATE POLICY resolution_review_read ON resolution_review
    FOR SELECT
    USING (
        docray_is_admin()
        OR mention_id IN (
            SELECT em.id
            FROM entity_mention em
            JOIN annotations a ON a.id = em.annotation_id
            WHERE a.document_id IN (SELECT * FROM docray_readable_documents())
        )
    );

DROP POLICY IF EXISTS resolution_review_write ON resolution_review;
CREATE POLICY resolution_review_write ON resolution_review
    FOR ALL
    USING (
        docray_is_admin()
        OR mention_id IN (
            SELECT em.id
            FROM entity_mention em
            JOIN annotations a ON a.id = em.annotation_id
            WHERE a.document_id IN (SELECT * FROM docray_writable_documents())
        )
    )
    WITH CHECK (
        docray_is_admin()
        OR mention_id IN (
            SELECT em.id
            FROM entity_mention em
            JOIN annotations a ON a.id = em.annotation_id
            WHERE a.document_id IN (SELECT * FROM docray_writable_documents())
        )
    );

-- The webapp/API read the review queue (docray_api inherits docray_client,
-- migration 030); the batch pipeline still owns the writes.
GRANT SELECT ON resolution_review TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('123_resolution_review_client_read.sql')
    ON CONFLICT (filename) DO NOTHING;
