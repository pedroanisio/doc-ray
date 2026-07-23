-- doc-ray: FORCE ROW LEVEL SECURITY + an explicit batch-writer identity (BL-138).
--
-- Every corpus table had ENABLE ROW LEVEL SECURITY; none had FORCE. Both the API
-- and the webapp connect as `docray`, which OWNS those tables, and Postgres
-- exempts a table owner from its own policies unless the table is forced. So any
-- code path that failed to SET ROLE read the corpus with no policy applied at
-- all — not merely as an anonymous caller. RLS the application tier can silently
-- opt out of is a convention, not a boundary.
--
-- Forcing RLS alone would stop ingest dead: the worker and every CLI connect as
-- the owner with no request principal, so docray_current_principal() is NULL and
-- the readable/writable sets are empty. The trusted batch writer is genuinely
-- trusted — it annotates documents across every tenant with no user on whose
-- behalf it acts — so it needs an exemption. This migration makes that exemption
-- EXPLICIT (a named role carrying BYPASSRLS) instead of incidental (whoever
-- happens to own the table). adapters/persistence.connect() drops to that role.
--
-- ============================ OPERATOR NOTE ================================
-- BYPASSRLS can only be set by a superuser. On a managed cluster the migration
-- runner (`docray`) is not one, so if this migration raises, run ONE statement
-- as the cluster superuser (`doadmin` on DigitalOcean) and re-run it:
--
--     ALTER ROLE docray_batch BYPASSRLS;
--
-- The migration deliberately RAISES rather than forcing RLS without a working
-- batch writer: a half-applied state stops every ingest and annotation job.
-- ===========================================================================
--
-- Why BYPASSRLS and not a policy branch for the batch writer (both were built;
-- the policy version was rejected on evidence):
--   1. INSERT is impossible to express. The write predicates are `id IN (%DOCS%)`
--      and %DOCS% selects FROM documents, so a brand-new row's id is never in
--      the set. Until now that was invisible — the owner bypassed the check.
--   2. RETURNING re-applies the SELECT policy. insert_document does
--      `INSERT ... RETURNING id`, and Postgres evaluates the SELECT policy on
--      returned rows; the new row is not in the stable snapshot the policy's
--      subquery sees, so it fails even with a working INSERT policy.
--   3. Fixing (2) needs a permissive SELECT policy, which OR-combines into the
--      read path and defeats the semi-join pullup that migration 018 documents
--      as load-bearing on lexeme_occurrences (~17.5M), spans (~29M) and tokens
--      (~25M). Trading a measured read-path regression for an access-control fix
--      is not a trade worth making when a role attribute expresses it exactly.

-- =============================== the identity ==============================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docray_batch') THEN
        CREATE ROLE docray_batch NOLOGIN;
    END IF;
END
$$;

-- Reached by SET ROLE from the owning application user, so the migration runner
-- must be able to assume it. Idempotent across re-runs.
DO $$
BEGIN
    EXECUTE format('GRANT docray_batch TO %I', current_user);
EXCEPTION
    WHEN duplicate_object OR invalid_grant_operation THEN NULL;
END
$$;

GRANT USAGE ON SCHEMA public TO docray_batch;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO docray_batch;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO docray_batch;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO docray_batch;

-- Attempt the attribute; succeeds when migrations run as a superuser (the
-- isolated test database), no-ops otherwise so the check below can report it.
DO $$
BEGIN
    ALTER ROLE docray_batch BYPASSRLS;
EXCEPTION
    WHEN insufficient_privilege THEN NULL;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docray_batch' AND rolbypassrls) THEN
        RAISE EXCEPTION
            'docray_batch lacks BYPASSRLS and this migration will not force RLS without it. '
            'Run as the cluster superuser, then re-run migrations:  ALTER ROLE docray_batch BYPASSRLS;';
    END IF;
END
$$;

-- ========================= force it everywhere =============================
-- Only tables that already opted into RLS are touched: enabling it somewhere it
-- was never enabled would be a policy decision, not a hardening step.

DO $$
DECLARE
    t record;
BEGIN
    FOR t IN
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity
    LOOP
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t.relname);
    END LOOP;
END
$$;

INSERT INTO schema_migrations (filename) VALUES ('106_force_rls.sql')
    ON CONFLICT (filename) DO NOTHING;
