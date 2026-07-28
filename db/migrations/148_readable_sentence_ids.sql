-- doc-ray: give sentence-text search its index back (BL-187).
--
-- SYMPTOM (production, 2026-07-28): an ordinary one-word search took 3,774 ms
-- to return a single page of 200 rows, reading all 1,966,114 sentences. Every
-- measured query class in the #47 evaluation planned a `Seq Scan` over
-- `sentences` (1,277 MB).
--
-- ROOT CAUSE. `sentences.text` has carried a GIN trigram index since migration
-- 002 (`idx_sentences_text_trgm`, 380 MB in production). Migration 018 put an
-- RLS `p_read` policy on the table. PostgreSQL will not evaluate a
-- non-leakproof qual ahead of a security qual, and both pattern operators are
-- non-leakproof (`textlike`, `texticlike`: `proleakproof = false`). Using the
-- index would mean testing "does this sentence match?" before "may this caller
-- see it?", which could reveal that a hidden document contains a search term.
-- So the planner refuses — and keeps refusing under `enable_seqscan = off`,
-- where it returns the sequential scan carrying the 1e10 disable penalty
-- because no alternative exists. The index has been unreachable for every
-- RLS-subject caller since migration 018, through more than a hundred
-- migrations, with nothing to signal it.
--
-- FIX. Candidate selection moves into a SECURITY DEFINER function owned by the
-- BYPASSRLS batch writer (migration 106/114), which applies the readable
-- document filter *explicitly* in SQL. RLS is then not in the planner's way and
-- the trigram index is reachable again.
--
-- WHY THIS IS STILL SAFE. `SECURITY DEFINER` changes `current_user`, but
-- `docray_effective_actor()` resolves the actor from the `role` GUC and
-- `session_user`, neither of which a definer function alters. The permission
-- filter therefore still resolves the *caller's* principal, not the batch
-- role's. `test_sentence_search_definer_db.py` pins that directly, including
-- the cross-principal isolation it implies; the trade being made is that this
-- filter is now the only one standing, so it is asserted rather than assumed.
--
-- SHAPE. Fixed arity with four scalar patterns, not `text[]`. Only separate
-- scalar `ILIKE` terms reach the trigram index; `ILIKE ALL (array)` and an
-- `unnest` form both fall back to a sequential scan (measured). Unused slots
-- are padded with '%', which matches everything and extracts no trigrams, so
-- padding costs nothing. Passing fewer patterns than a phrase has words is
-- correct: this is a candidate *superset*, and the pure `surface_word_v1`
-- verifier removes the false positives.
--
-- NOT FIXED BY THIS. A term under three characters ('of', 'it') yields no
-- trigram and still scans. That is inherent to pg_trgm, and it is already the
-- cheap case because a non-selective predicate fills the candidate cap
-- immediately and the LIMIT stops the scan.

CREATE OR REPLACE FUNCTION docray_readable_sentence_ids(
    pattern_1   text,
    pattern_2   text,
    pattern_3   text,
    pattern_4   text,
    document    uuid,
    max_rows    int
)
RETURNS TABLE (id ulid, text text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT s.id, s.text
      FROM sentences s
     WHERE s.document_id IN (SELECT docray_readable_documents())
       AND (document IS NULL OR s.document_id = document)
       AND s.text ILIKE pattern_1
       AND s.text ILIKE pattern_2
       AND s.text ILIKE pattern_3
       AND s.text ILIKE pattern_4
     LIMIT max_rows
$$;

COMMENT ON FUNCTION docray_readable_sentence_ids(text, text, text, text, uuid, int) IS
    'Bounded candidate sentences matching every pattern, restricted to the '
    'CALLER''s readable documents. SECURITY DEFINER so the trigram index on '
    'sentences.text is reachable: RLS forbids a non-leakproof ILIKE ahead of '
    'its security qual. Returns a superset; callers verify with surface_word_v1.';

-- Must be BYPASSRLS-owned, or the body re-enters the very policy it resolves
-- (migration 114's stack-depth outage). Assigned explicitly rather than relying
-- on 114's sweep, so a fresh database is correct without replaying it.
DO $$
BEGIN
    ALTER FUNCTION docray_readable_sentence_ids(text, text, text, text, uuid, int)
        OWNER TO docray_batch;
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE EXCEPTION
            'cannot assign docray_readable_sentence_ids to docray_batch; run this '
            'migration as a role that can, then re-run (see migration 114)';
END
$$;

REVOKE ALL ON FUNCTION docray_readable_sentence_ids(text, text, text, text, uuid, int)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION docray_readable_sentence_ids(text, text, text, text, uuid, int)
    TO docray_api, docray_batch;

-- Guard: a definer function that reads a forced-RLS table and is NOT
-- BYPASSRLS-owned is a latent recursion outage, so refuse a half-applied state.
DO $$
DECLARE
    owner_bypasses boolean;
BEGIN
    SELECT r.rolbypassrls
      INTO owner_bypasses
      FROM pg_proc p
      JOIN pg_roles r ON r.oid = p.proowner
     WHERE p.proname = 'docray_readable_sentence_ids'
       AND p.pronamespace = 'public'::regnamespace;

    IF owner_bypasses IS DISTINCT FROM true THEN
        RAISE EXCEPTION
            'docray_readable_sentence_ids must be owned by a BYPASSRLS role '
            '(recursion + dead-index risk)';
    END IF;
END
$$;

INSERT INTO schema_migrations (filename) VALUES ('148_readable_sentence_ids.sql')
    ON CONFLICT (filename) DO NOTHING;
