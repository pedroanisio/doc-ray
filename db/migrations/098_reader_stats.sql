-- doc-ray: reader-page rollups — stop aggregating lexeme_occurrences on read.
--
-- Same disease as migration 095 (the corpus stat band): pages recomputed
-- aggregates over the 17.5M-row lexeme_occurrences table on every request.
-- Measured as docray_api with an admin principal:
--   * The Lexicon (/lexemes) joined lexemes -> lexeme_occurrences, GROUP BY
--     lexeme, ORDER BY uses DESC on every load: ~37 s.
--   * The document reader sidebar recomputed a document's POS distribution
--     (~740 ms) and top verbs (~464 ms) on every page navigation.
--
-- FIX — precompute, read on the hot path (all refreshed by the trusted batch
-- writer, never on the read path):
--   * lexeme_stats(tenant_id, lexeme_uid, uses) — per-tenant usage counts, keyed
--     PER TENANT (never one global row) so it cannot leak one tenant's vocabulary
--     across tenants; ordered read via (tenant_id, uses DESC). The Lexicon list
--     drops to ~2 ms.
--   * document_stats gains pos_counts / top_verbs (JSONB, document-level, exact
--     per principal via the existing document_id RLS) so the reader sidebar reads
--     one row instead of two whole-document aggregations.
--
-- FRESHNESS: document_stats is refreshed exactly per affected document; the
-- per-tenant lexeme_stats recompute shares the corpus_stats debounce window
-- (default 10 min) so a bulk re-annotation pays it once, not once per document.
-- Backfilled exactly at deploy time below.

-- =============================== schema ====================================

ALTER TABLE document_stats ADD COLUMN IF NOT EXISTS pos_counts jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE document_stats ADD COLUMN IF NOT EXISTS top_verbs  jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS lexeme_stats (
    tenant_id   uuid   NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    lexeme_uid  text   NOT NULL REFERENCES lexemes(uid) ON DELETE CASCADE,
    uses        bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, lexeme_uid)
);
-- Ranked read for the Lexicon list (top lexemes by usage within a tenant).
CREATE INDEX IF NOT EXISTS idx_lexeme_stats_tenant_uses
    ON lexeme_stats (tenant_id, uses DESC);

-- =============================== refresh helpers ===========================
-- SECURITY DEFINER + pinned search_path: recompute reads base tables without
-- RLS (owner), never granted to docray_client. CREATE OR REPLACE of the two
-- functions migration 095 defined, extended for the new columns/table.

CREATE OR REPLACE FUNCTION docray_refresh_document_stats(doc uuid) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS
$$
    INSERT INTO document_stats (
        document_id, n_sentences, n_lexeme_occurrences, pos_counts, top_verbs, updated_at
    )
    VALUES (
        doc,
        (SELECT count(*) FROM sentences WHERE document_id = doc),
        (SELECT count(*)
           FROM lexeme_occurrences lo
           JOIN annotations a ON a.id = lo.annotation_id
          WHERE a.document_id = doc),
        COALESCE((
            SELECT jsonb_agg(jsonb_build_object('pos', pos, 'n', n) ORDER BY n DESC, pos)
              FROM (SELECT l.pos, count(*) AS n
                      FROM lexeme_occurrences o
                      JOIN annotations a ON a.id = o.annotation_id
                      JOIN lexemes l     ON l.uid = o.lexeme_uid
                     WHERE a.document_id = doc
                     GROUP BY l.pos) p
        ), '[]'::jsonb),
        COALESCE((
            SELECT jsonb_agg(jsonb_build_object('uid', uid, 'lemma', lemma, 'n', n))
              FROM (SELECT l.uid, l.lemma, count(*) AS n
                      FROM lexeme_occurrences o
                      JOIN annotations a ON a.id = o.annotation_id
                      JOIN lexemes l     ON l.uid = o.lexeme_uid
                     WHERE a.document_id = doc AND l.pos = 'VERB'
                     GROUP BY l.uid, l.lemma
                     ORDER BY count(*) DESC, l.lemma
                     LIMIT 15) v
        ), '[]'::jsonb),
        now()
    )
    ON CONFLICT (document_id) DO UPDATE
        SET n_sentences = EXCLUDED.n_sentences,
            n_lexeme_occurrences = EXCLUDED.n_lexeme_occurrences,
            pos_counts = EXCLUDED.pos_counts,
            top_verbs = EXCLUDED.top_verbs,
            updated_at = now();
$$;

CREATE OR REPLACE FUNCTION docray_refresh_lexeme_stats(tenant uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS
$fn$
BEGIN
    DELETE FROM lexeme_stats WHERE tenant_id = tenant;
    INSERT INTO lexeme_stats (tenant_id, lexeme_uid, uses)
    SELECT tenant, o.lexeme_uid, count(*)
      FROM lexeme_occurrences o
      JOIN annotations a ON a.id = o.annotation_id
      JOIN documents d   ON d.id = a.document_id
     WHERE d.tenant_id = tenant
     GROUP BY o.lexeme_uid;
END;
$fn$;

-- Extend the per-document entry point (migration 095) to also refresh the
-- tenant's distinct-lexeme scalar AND per-lexeme usage counts, sharing one
-- debounce window so bulk (re)annotation pays the tenant-wide recompute once.
CREATE OR REPLACE FUNCTION docray_refresh_stats_for_document(
    doc uuid,
    corpus_max_age interval DEFAULT interval '10 minutes'
) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS
$fn$
DECLARE
    t     uuid;
    fresh boolean;
BEGIN
    PERFORM docray_refresh_document_stats(doc);
    SELECT tenant_id INTO t FROM documents WHERE id = doc;
    IF t IS NULL THEN
        RETURN;
    END IF;
    SELECT computed_at > now() - corpus_max_age INTO fresh
      FROM corpus_stats WHERE tenant_id = t;
    IF fresh IS TRUE THEN
        RETURN;
    END IF;
    PERFORM docray_refresh_corpus_stats(t);
    PERFORM docray_refresh_lexeme_stats(t);
END;
$fn$;

-- Backfill / full rebuild. SET-BASED (never a per-document loop): the reader
-- rollups are computed in a few passes over lexeme_occurrences rather than one
-- scan per document, so a corpus of hundreds of documents rebuilds in seconds.
CREATE OR REPLACE FUNCTION docray_refresh_all_corpus_stats() RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS
$fn$
BEGIN
    -- document_stats: counts + per-document POS distribution and top verbs.
    WITH sent AS (
        SELECT document_id, count(*) AS n FROM sentences GROUP BY document_id
    ),
    occ_pos AS (
        SELECT a.document_id, l.pos, count(*) AS n
          FROM lexeme_occurrences o
          JOIN annotations a ON a.id = o.annotation_id
          JOIN lexemes l     ON l.uid = o.lexeme_uid
         GROUP BY a.document_id, l.pos
    ),
    occ_tot AS (
        SELECT document_id, sum(n) AS n FROM occ_pos GROUP BY document_id
    ),
    pos_json AS (
        SELECT document_id,
               jsonb_agg(jsonb_build_object('pos', pos, 'n', n) ORDER BY n DESC, pos) AS j
          FROM occ_pos GROUP BY document_id
    ),
    verbs_ranked AS (
        SELECT a.document_id, l.uid, l.lemma, count(*) AS n,
               row_number() OVER (PARTITION BY a.document_id ORDER BY count(*) DESC, l.lemma) AS rn
          FROM lexeme_occurrences o
          JOIN annotations a ON a.id = o.annotation_id
          JOIN lexemes l     ON l.uid = o.lexeme_uid
         WHERE l.pos = 'VERB'
         GROUP BY a.document_id, l.uid, l.lemma
    ),
    verbs_json AS (
        SELECT document_id,
               jsonb_agg(jsonb_build_object('uid', uid, 'lemma', lemma, 'n', n) ORDER BY rn) AS j
          FROM verbs_ranked WHERE rn <= 15 GROUP BY document_id
    )
    INSERT INTO document_stats (
        document_id, n_sentences, n_lexeme_occurrences, pos_counts, top_verbs, updated_at
    )
    SELECT d.id, COALESCE(s.n, 0), COALESCE(ot.n, 0),
           COALESCE(pj.j, '[]'::jsonb), COALESCE(vj.j, '[]'::jsonb), now()
      FROM documents d
      LEFT JOIN sent s        ON s.document_id = d.id
      LEFT JOIN occ_tot ot    ON ot.document_id = d.id
      LEFT JOIN pos_json pj   ON pj.document_id = d.id
      LEFT JOIN verbs_json vj ON vj.document_id = d.id
    ON CONFLICT (document_id) DO UPDATE
        SET n_sentences = EXCLUDED.n_sentences,
            n_lexeme_occurrences = EXCLUDED.n_lexeme_occurrences,
            pos_counts = EXCLUDED.pos_counts,
            top_verbs = EXCLUDED.top_verbs,
            updated_at = now();

    -- corpus_stats: per-tenant distinct-lexeme scalar.
    INSERT INTO corpus_stats (tenant_id, n_distinct_lexemes, computed_at)
    SELECT dd.tenant_id, count(DISTINCT lo.lexeme_uid), now()
      FROM documents dd
      JOIN annotations a         ON a.document_id = dd.id
      JOIN lexeme_occurrences lo ON lo.annotation_id = a.id
     GROUP BY dd.tenant_id
    ON CONFLICT (tenant_id) DO UPDATE
        SET n_distinct_lexemes = EXCLUDED.n_distinct_lexemes,
            computed_at = now();

    -- lexeme_stats: per-(tenant, lexeme) usage counts (full rebuild).
    DELETE FROM lexeme_stats;
    INSERT INTO lexeme_stats (tenant_id, lexeme_uid, uses)
    SELECT d.tenant_id, o.lexeme_uid, count(*)
      FROM lexeme_occurrences o
      JOIN annotations a ON a.id = o.annotation_id
      JOIN documents d   ON d.id = a.document_id
     GROUP BY d.tenant_id, o.lexeme_uid;
END;
$fn$;

REVOKE ALL ON FUNCTION docray_refresh_lexeme_stats(uuid) FROM PUBLIC;

-- =============================== attach RLS ================================
-- lexeme_stats has no document_id; a bespoke tenant-scoped read policy exposes
-- only the caller's own tenant row set (matches corpus_stats in migration 095).

ALTER TABLE lexeme_stats ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_read ON lexeme_stats;
CREATE POLICY p_read ON lexeme_stats FOR SELECT
    USING (tenant_id = docray_current_tenant());

GRANT SELECT ON lexeme_stats TO docray_client;

-- =============================== backfill ==================================
SELECT docray_refresh_all_corpus_stats();

INSERT INTO schema_migrations (filename) VALUES ('098_reader_stats.sql')
    ON CONFLICT (filename) DO NOTHING;
