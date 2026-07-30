-- doc-ray: make the total-lexeme-uses backfill actually write (BL-204).
--
-- Migration 152 added `corpus_stats.total_lexeme_uses` and ended with a plain
--
--     UPDATE corpus_stats cs SET total_lexeme_uses = (SELECT ... FROM lexeme_stats ...)
--
-- That statement wrote NOTHING, and raised nothing while doing so. `corpus_stats`
-- carries FORCE ROW LEVEL SECURITY with a single `p_read` SELECT policy and no
-- UPDATE policy at all, and the migration runner connects as `docray`, which is
-- not BYPASSRLS. Under FORCE RLS an UPDATE with no permissive UPDATE policy
-- matches zero rows — a clean success. Verified on production immediately after
-- 152 applied: the column existed and was NULL for the only tenant, while the
-- true value was 72,681,387 over 453,662 lexeme_stats rows.
--
-- A NULL denominator is not inert. `_TERM_FREQ_SQL` coalesces it to 0, so
-- `term_frequency` would return uses>0 against totalUses=0 — a share that is
-- either meaningless or zero — which is precisely the "maintained scalar nobody
-- maintains" failure 152's own comment warned about, made harder to spot because
-- the migration reports success.
--
-- The fix is the pattern the rest of this schema already uses for tenant rollups:
-- `docray_refresh_corpus_stats` works only because it is SECURITY DEFINER owned
-- by `docray_batch`, the one role carrying BYPASSRLS. The backfill gets the same
-- treatment, as a named function rather than an inline statement so an operator
-- can re-run it after a restore without re-deriving the whole rollup (the
-- `count(DISTINCT lexeme_uid)` over 17.5M lexeme_occurrences rows that 152
-- deliberately refused to drag into a schema change).
--
-- Idempotent: re-running recomputes the same scalar from maintained inputs.

CREATE OR REPLACE FUNCTION docray_backfill_total_lexeme_uses()
RETURNS bigint
LANGUAGE sql
SECURITY DEFINER
SET search_path TO 'pg_catalog', 'public'
AS $function$
    WITH updated AS (
        UPDATE corpus_stats cs
           SET total_lexeme_uses = (
                 SELECT COALESCE(SUM(ls.uses), 0)
                   FROM lexeme_stats ls
                  WHERE ls.tenant_id = cs.tenant_id
               )
        RETURNING 1
    )
    SELECT count(*)::bigint FROM updated;
$function$;

-- Ownership is the whole mechanism, not bookkeeping: SECURITY DEFINER runs as the
-- OWNER, and only `docray_batch` has BYPASSRLS. Owned by `docray` this function
-- would be as inert as the statement it replaces.
ALTER FUNCTION docray_backfill_total_lexeme_uses() OWNER TO docray_batch;

COMMENT ON FUNCTION docray_backfill_total_lexeme_uses() IS
    'Recompute corpus_stats.total_lexeme_uses for every tenant from lexeme_stats. '
    'SECURITY DEFINER owned by docray_batch because corpus_stats has FORCE RLS and '
    'no UPDATE policy (BL-204). Safe to re-run.';

SELECT docray_backfill_total_lexeme_uses();

INSERT INTO schema_migrations (filename)
VALUES ('156_backfill_total_lexeme_uses.sql')
ON CONFLICT (filename) DO NOTHING;
