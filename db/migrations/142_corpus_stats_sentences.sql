-- doc-ray: the tenant's sentence total on the corpus rollup (#39).
--
-- WHY: the term-frequency pre-flight now answers multi-word terms with a bounded
--   count of matching sentences, and a count needs a denominator. The read path
--   must never learn that denominator by counting `sentences` live — that is the
--   D-024 fact-table scan migration 098 exists to prevent. So the total joins the
--   existing per-tenant rollup and is read as one scalar.
--
-- ROLLUP-FROM-ROLLUP: n_sentences sums `document_stats.n_sentences` over the
--   tenant's documents rather than counting `sentences` directly — the same
--   doctrine 095/098 established, so the refresh stays cheap and the two rollups
--   cannot disagree.
--
-- FRESHNESS: unchanged from 095. Refreshes run in the trusted batch writer, never
--   on the read path; the per-tenant scalar is debounced. Eventually consistent by
--   design, backfilled exactly at deploy time by the call at the end of this file.

ALTER TABLE corpus_stats ADD COLUMN IF NOT EXISTS n_sentences bigint NOT NULL DEFAULT 0;

-- Extends 095's helper: same SECURITY DEFINER + pinned search_path, one more
-- column. The lexeme half is unchanged, so an existing caller sees no difference.
CREATE OR REPLACE FUNCTION docray_refresh_corpus_stats(tenant uuid) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS
$$
    INSERT INTO corpus_stats (tenant_id, n_distinct_lexemes, n_sentences, computed_at)
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
           now()
    ON CONFLICT (tenant_id) DO UPDATE
        SET n_distinct_lexemes = EXCLUDED.n_distinct_lexemes,
            n_sentences = EXCLUDED.n_sentences,
            computed_at = now();
$$;

REVOKE ALL ON FUNCTION docray_refresh_corpus_stats(uuid) FROM PUBLIC;

-- Re-assert migration 114's invariant. This helper is SECURITY DEFINER and reads
-- `documents` + `document_stats`, both under FORCE ROW LEVEL SECURITY (106), so it
-- must run under a BYPASSRLS owner or it silently rolls up only the rows its own
-- execution context can see — an undercount that would surface as a wrong
-- `totalSentences` denominator, not as an error. 114 swept the functions that
-- existed then; a later CREATE OR REPLACE has to restate it.
DO $$
BEGIN
    ALTER FUNCTION docray_refresh_corpus_stats(uuid) OWNER TO docray_batch;
EXCEPTION
    WHEN insufficient_privilege OR undefined_object THEN NULL;
END
$$;

-- Recompute after the ownership fix so the backfill below is the corrected number.

-- Backfill every tenant at deploy time, as 095/098 do.
SELECT docray_refresh_all_corpus_stats();

INSERT INTO schema_migrations (filename)
VALUES ('142_corpus_stats_sentences.sql')
ON CONFLICT (filename) DO NOTHING;
