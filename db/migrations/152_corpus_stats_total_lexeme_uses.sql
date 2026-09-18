-- doc-ray: maintained total-lexeme-uses denominator (BL-204).
--
-- `term_frequency` computed its share denominator as a live
-- `SUM(uses) FROM lexeme_stats` on every call — the read-path fact-table
-- aggregate D-024 forbids. Forced RLS turns that from slow into unusable,
-- because the policy is evaluated per row across the whole table. Measured on
-- a corpus with ~450k lexeme_stats rows:
--
--     docray_batch (BYPASSRLS)          2.0 s
--     docray_api + principal context   22.9 s   <- the real API path
--
-- Against the API's 15 s statement_timeout every call failed, including single
-- ordinary words, and agents abandoned the tool.
--
-- This is the same fix migration 142 applied to the sentence denominator: keep
-- the scalar on the tenant rollup and read it, instead of deriving it per call.
--
-- Additive: one nullable column plus a redefinition of the existing refresh
-- functions. No policy, table or signature changes, so a rolling deploy reads
-- old rows unchanged — a NULL column simply means "not yet refreshed", which the
-- reader coalesces to 0.

ALTER TABLE corpus_stats
    ADD COLUMN IF NOT EXISTS total_lexeme_uses bigint;

COMMENT ON COLUMN corpus_stats.total_lexeme_uses IS
    'Maintained SUM(lexeme_stats.uses) for the tenant — term_frequency''s share '
    'denominator. Never computed on a read path (D-024, BL-204).';

-- Redefine the per-tenant refresh so the new scalar is maintained wherever the
-- other rollups are. Body is migration 146''s, plus the new column.
CREATE OR REPLACE FUNCTION docray_refresh_corpus_stats(tenant uuid)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path TO 'pg_catalog', 'public'
AS $function$
    INSERT INTO corpus_stats (
        tenant_id, n_distinct_lexemes, n_sentences, total_lexeme_uses, computed_at
    )
    SELECT tenant,
           (SELECT count(DISTINCT lo.lexeme_uid)
              FROM documents d
              JOIN annotations a         ON a.document_id = d.id
              JOIN lexeme_occurrences lo ON lo.annotation_id = a.id
             WHERE d.tenant_id = tenant),
           (SELECT COALESCE(SUM(ds.n_sentences), 0)
              FROM documents d
              JOIN document_stats ds ON ds.document_id = d.id
             WHERE d.tenant_id = tenant),
           (SELECT COALESCE(SUM(ls.uses), 0)
              FROM lexeme_stats ls
             WHERE ls.tenant_id = tenant),
           now()
    ON CONFLICT (tenant_id) DO UPDATE
        SET n_distinct_lexemes = EXCLUDED.n_distinct_lexemes,
            n_sentences        = EXCLUDED.n_sentences,
            total_lexeme_uses  = EXCLUDED.total_lexeme_uses,
            computed_at        = now();
$function$;

-- Backfill ONLY the new column. Calling docray_refresh_corpus_stats here would
-- also recompute n_distinct_lexemes, whose `count(DISTINCT lo.lexeme_uid)` joins
-- the 17.5M-row lexeme_occurrences table — that backfill ran past ten minutes
-- and had to be abandoned. Summing lexeme_stats per tenant is ~2 s, and it is
-- the only value this migration introduces; the other columns are already
-- maintained and must not be dragged into a schema change.
UPDATE corpus_stats cs
   SET total_lexeme_uses = (
         SELECT COALESCE(SUM(ls.uses), 0)
           FROM lexeme_stats ls
          WHERE ls.tenant_id = cs.tenant_id
       );

INSERT INTO schema_migrations (filename)
VALUES ('152_corpus_stats_total_lexeme_uses.sql')
ON CONFLICT (filename) DO NOTHING;
