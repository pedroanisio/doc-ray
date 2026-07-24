-- doc-ray: SECURITY DEFINER RLS helpers must run with BYPASSRLS (BL-142).
--
-- SYMPTOM (production): a non-admin member browsing their corpus got a 500 —
--   stack depth limit exceeded
--   CONTEXT: SQL function "docray_readable_documents" statement 1  (× hundreds)
--
-- ROOT CAUSE. docray_readable_documents() (and its siblings) are SECURITY
-- DEFINER functions that query `FROM documents`. Migration 106 put every corpus
-- table under FORCE ROW LEVEL SECURITY, so even the table owner is subject to
-- the policy. The documents SELECT policy is `docray_is_admin() OR id IN
-- (SELECT docray_readable_documents())`. For an admin the first branch
-- short-circuits; for a NON-admin it evaluates the subquery, which runs
-- docray_readable_documents, whose own `FROM documents` re-enters the policy →
-- docray_readable_documents again → unbounded recursion.
--
-- Why it never showed in CI: the isolated test DB runs migrations as a
-- SUPERUSER, so these functions are superuser-owned and their SECURITY DEFINER
-- body bypasses RLS — no recursion. On managed Postgres the owner is the
-- non-superuser `docray`, so the body is subject to RLS and recurses. Same
-- managed-vs-superuser gap as migrations 032/106.
--
-- FIX. These helpers compute an access decision (or refresh corpus-wide stats),
-- so they are MEANT to see every row regardless of the caller — exactly what a
-- BYPASSRLS owner gives. Reassign every SECURITY DEFINER function that reads a
-- forced-RLS table to the batch writer docray_batch (BYPASSRLS, migration 106).
-- Done dynamically so helpers added by later migrations are covered too.

-- The new owner must hold CREATE on the schema. On a superuser-run DB this
-- GRANT succeeds; on managed Postgres, where docray is not the schema owner, it
-- raises — the operator runs it once as the cluster superuser (see the RAISE
-- below) and re-runs. Guarded so the reassignment can proceed when it is already
-- in place.
DO $$
BEGIN
    GRANT CREATE ON SCHEMA public TO docray_batch;
EXCEPTION
    WHEN insufficient_privilege THEN NULL;
END
$$;

DO $$
DECLARE
    fn record;
BEGIN
    FOR fn IN
        SELECT p.oid AS oid, p.oid::regprocedure::text AS sig
          FROM pg_proc p
          JOIN pg_roles r ON r.oid = p.proowner
         WHERE p.prosecdef
           AND p.pronamespace = 'public'::regnamespace
           AND NOT r.rolbypassrls
    LOOP
        IF EXISTS (
            SELECT 1
              FROM pg_class c
             WHERE c.relkind = 'r'
               AND c.relrowsecurity
               AND c.relforcerowsecurity
               AND c.relnamespace = 'public'::regnamespace
               AND pg_get_functiondef(fn.oid) ~ ('\y' || c.relname || '\y')
        ) THEN
            BEGIN
                EXECUTE format('ALTER FUNCTION %s OWNER TO docray_batch', fn.sig);
            EXCEPTION
                WHEN insufficient_privilege THEN
                    RAISE EXCEPTION
                        'cannot reassign % to docray_batch: it lacks CREATE on schema public. '
                        'Run as the cluster superuser (doadmin on DigitalOcean), then re-run '
                        'migrations:  GRANT CREATE ON SCHEMA public TO docray_batch;', fn.sig;
            END;
        END IF;
    END LOOP;
END
$$;

-- Guard: fail loudly if any SECURITY DEFINER function that reads a forced-RLS
-- table is still owned by a non-BYPASSRLS role — that is a latent stack-depth
-- outage, so a half-applied state must not be reachable.
DO $$
DECLARE
    leftover text;
BEGIN
    SELECT string_agg(p.oid::regprocedure::text, ', ')
      INTO leftover
      FROM pg_proc p
      JOIN pg_roles r ON r.oid = p.proowner
     WHERE p.prosecdef
       AND p.pronamespace = 'public'::regnamespace
       AND NOT r.rolbypassrls
       AND EXISTS (
            SELECT 1
              FROM pg_class c
             WHERE c.relkind = 'r'
               AND c.relrowsecurity
               AND c.relforcerowsecurity
               AND c.relnamespace = 'public'::regnamespace
               AND pg_get_functiondef(p.oid) ~ ('\y' || c.relname || '\y')
       );
    IF leftover IS NOT NULL THEN
        RAISE EXCEPTION
            'SECURITY DEFINER functions that read a forced-RLS table are still non-BYPASSRLS '
            'owned (recursion risk): %', leftover;
    END IF;
END
$$;

INSERT INTO schema_migrations (filename) VALUES ('114_rls_helper_bypass_owner.sql')
    ON CONFLICT (filename) DO NOTHING;
