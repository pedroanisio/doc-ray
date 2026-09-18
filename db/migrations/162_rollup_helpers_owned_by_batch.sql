-- doc-ray: the per-document rollup helpers must be owned by docray_batch (BL-206).
--
-- Migration 154 created
--
--     docray_refresh_document_lexeme_stats(uuid)
--     docray_refresh_document_entity_frequency_stats(uuid)
--     docray_unapply_document_rollups(uuid)
--
-- as SECURITY DEFINER but never assigned their owner, so they belong to whichever
-- role applied the migration — on a managed cluster `docray`, which is not
-- BYPASSRLS. Migration 114's one-time sweep reassigned every definer that existed
-- at that moment and cannot reach functions defined later; migration 159 made
-- these three callable without touching who they run as.
--
-- SECURITY DEFINER runs the body as the OWNER. Owned by `docray`, each helper
-- reads its inputs (`annotations`, `lexeme_occurrences`, `entity_mention`) through
-- forced RLS with no tenant and no principal, sees zero rows, computes a delta of
-- "this document contributes nothing", and its `WHERE diff <> 0` selects nothing
-- to write. No row is touched, so no policy fires and no error is raised: the
-- driver stamps `corpus_stats.computed_at` fresh and the rollups quietly stop
-- following the corpus.
--
-- Ownership is the whole mechanism (as migration 156 notes for its own helper):
-- only `docray_batch` bypasses RLS, so only a helper it owns can read what it must
-- diff. Assigned here explicitly, then verified loudly, then the rollups every
-- tenant depends on are rebuilt once from the maintained inputs — the deltas
-- lost while the helpers were inert cannot be replayed.
--
-- Idempotent: ALTER FUNCTION ... OWNER TO is a no-op when already so owned, and
-- the full refresh recomputes the same figures from the same rows.

DO $$
BEGIN
    IF to_regrole('docray_batch') IS NULL THEN
        -- Role-absent minimal development databases keep their migration owner;
        -- every production/test database creates docray_batch in migration 106.
        RETURN;
    END IF;
    BEGIN
        ALTER FUNCTION docray_refresh_document_lexeme_stats(uuid) OWNER TO docray_batch;
        ALTER FUNCTION docray_refresh_document_entity_frequency_stats(uuid) OWNER TO docray_batch;
        ALTER FUNCTION docray_unapply_document_rollups(uuid) OWNER TO docray_batch;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                'cannot reassign the per-document rollup helpers to docray_batch. '
                'Run as the cluster superuser (doadmin on DigitalOcean), or grant the '
                'applying role membership: GRANT docray_batch TO <role>; then re-run migrations.';
    END;
END
$$;

-- Fail loudly if any of the three is still owned by a non-BYPASSRLS role: a
-- half-applied state is exactly the silent failure this migration removes.
DO $$
DECLARE
    invalid_helpers text;
BEGIN
    IF to_regrole('docray_batch') IS NULL THEN
        RETURN;
    END IF;

    SELECT string_agg(
               format('%s (owner=%s, bypassrls=%s, security_definer=%s)',
                      p.oid::regprocedure, r.rolname, r.rolbypassrls, p.prosecdef),
               ', '
           )
      INTO invalid_helpers
      FROM pg_proc p
      JOIN pg_roles r ON r.oid = p.proowner
     WHERE p.oid IN (
               'docray_refresh_document_lexeme_stats(uuid)'::regprocedure,
               'docray_refresh_document_entity_frequency_stats(uuid)'::regprocedure,
               'docray_unapply_document_rollups(uuid)'::regprocedure
           )
       AND (r.rolname <> 'docray_batch' OR NOT r.rolbypassrls OR NOT p.prosecdef);

    IF invalid_helpers IS NOT NULL THEN
        RAISE EXCEPTION
            'per-document rollup helpers violate the batch-owner/RLS invariant: %',
            invalid_helpers;
    END IF;
END
$$;

-- Rebuild every tenant's rollups once from the maintained inputs: the deltas the
-- inert helpers failed to apply are not recoverable any other way. Same call as
-- migrations 142, 146 and 154 made at deploy time.
SELECT docray_refresh_all_corpus_stats();

INSERT INTO schema_migrations (filename)
VALUES ('162_rollup_helpers_owned_by_batch.sql')
ON CONFLICT (filename) DO NOTHING;
