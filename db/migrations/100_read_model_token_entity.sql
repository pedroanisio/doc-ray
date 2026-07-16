-- doc-ray: extend the read-model rollups (D-024) with token counts + entity
-- frequency, so the GraphQL/MCP layer stops aggregating fact tables on read.
--
-- Remaining offenders after migrations 095-099 (per the audit + the D-024 gate):
--   * Document.tokenCount / token_counts_by_documents -> live count(*) over the
--     25M-row tokens table.
--   * entityFrequency(documentId: null) -> corpus-wide GROUP BY over the 1.1M-row
--     entity_mention table, ordered by a computed count.
--
-- FIX — two more precomputed rollups, refreshed at the pipeline boundary:
--   * document_stats.n_tokens (document-level, exact per principal via the
--     existing document_id RLS).
--   * entity_frequency_stats(tenant_id, entity_text, entity_type, uses) — per
--     tenant (never global), ranked read via (tenant_id, uses DESC), for the
--     corpus-wide entityFrequency; the document-scoped call stays live (bounded).

-- =============================== schema ====================================

ALTER TABLE document_stats ADD COLUMN IF NOT EXISTS n_tokens bigint NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS entity_frequency_stats (
    tenant_id    uuid   NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    entity_text  text   NOT NULL,
    entity_type  text   NOT NULL,
    uses         bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, entity_text, entity_type)
);
CREATE INDEX IF NOT EXISTS idx_entity_freq_tenant_uses
    ON entity_frequency_stats (tenant_id, uses DESC);

-- =============================== refresh helpers ===========================

CREATE OR REPLACE FUNCTION docray_refresh_entity_frequency_stats(tenant uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS
$fn$
BEGIN
    DELETE FROM entity_frequency_stats WHERE tenant_id = tenant;
    INSERT INTO entity_frequency_stats (tenant_id, entity_text, entity_type, uses)
    SELECT tenant, em.surface, em.entity_type, count(*)
      FROM entity_mention em
      JOIN annotations a ON a.id = em.annotation_id
      JOIN documents d   ON d.id = a.document_id
     WHERE d.tenant_id = tenant
     GROUP BY em.surface, em.entity_type;
END;
$fn$;

-- Extend the per-document refresh to also stamp n_tokens (migration 098 form + tokens).
CREATE OR REPLACE FUNCTION docray_refresh_document_stats(doc uuid) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS
$$
    INSERT INTO document_stats (
        document_id, n_sentences, n_lexeme_occurrences, n_tokens, pos_counts, top_verbs, updated_at
    )
    VALUES (
        doc,
        (SELECT count(*) FROM sentences WHERE document_id = doc),
        (SELECT count(*)
           FROM lexeme_occurrences lo
           JOIN annotations a ON a.id = lo.annotation_id
          WHERE a.document_id = doc),
        (SELECT count(*)
           FROM tokens t
           JOIN annotations a ON a.id = t.annotation_id
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
            n_tokens = EXCLUDED.n_tokens,
            pos_counts = EXCLUDED.pos_counts,
            top_verbs = EXCLUDED.top_verbs,
            updated_at = now();
$$;

-- Extend the debounced tenant refresh to also rebuild entity frequency.
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
    PERFORM docray_refresh_entity_frequency_stats(t);
END;
$fn$;

-- Full rebuild: add the tokens pass + entity-frequency rebuild to migration 098's.
CREATE OR REPLACE FUNCTION docray_refresh_all_corpus_stats() RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS
$fn$
DECLARE
    t uuid;
BEGIN
    WITH sent AS (
        SELECT document_id, count(*) AS n FROM sentences GROUP BY document_id
    ),
    tok AS (
        SELECT a.document_id, count(*) AS n
          FROM tokens tk JOIN annotations a ON a.id = tk.annotation_id
         GROUP BY a.document_id
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
        document_id, n_sentences, n_lexeme_occurrences, n_tokens, pos_counts, top_verbs, updated_at
    )
    SELECT d.id, COALESCE(s.n, 0), COALESCE(ot.n, 0), COALESCE(tk.n, 0),
           COALESCE(pj.j, '[]'::jsonb), COALESCE(vj.j, '[]'::jsonb), now()
      FROM documents d
      LEFT JOIN sent s        ON s.document_id = d.id
      LEFT JOIN tok tk        ON tk.document_id = d.id
      LEFT JOIN occ_tot ot    ON ot.document_id = d.id
      LEFT JOIN pos_json pj   ON pj.document_id = d.id
      LEFT JOIN verbs_json vj ON vj.document_id = d.id
    ON CONFLICT (document_id) DO UPDATE
        SET n_sentences = EXCLUDED.n_sentences,
            n_lexeme_occurrences = EXCLUDED.n_lexeme_occurrences,
            n_tokens = EXCLUDED.n_tokens,
            pos_counts = EXCLUDED.pos_counts,
            top_verbs = EXCLUDED.top_verbs,
            updated_at = now();

    INSERT INTO corpus_stats (tenant_id, n_distinct_lexemes, computed_at)
    SELECT dd.tenant_id, count(DISTINCT lo.lexeme_uid), now()
      FROM documents dd
      JOIN annotations a         ON a.document_id = dd.id
      JOIN lexeme_occurrences lo ON lo.annotation_id = a.id
     GROUP BY dd.tenant_id
    ON CONFLICT (tenant_id) DO UPDATE
        SET n_distinct_lexemes = EXCLUDED.n_distinct_lexemes,
            computed_at = now();

    DELETE FROM lexeme_stats;
    INSERT INTO lexeme_stats (tenant_id, lexeme_uid, uses)
    SELECT d.tenant_id, o.lexeme_uid, count(*)
      FROM lexeme_occurrences o
      JOIN annotations a ON a.id = o.annotation_id
      JOIN documents d   ON d.id = a.document_id
     GROUP BY d.tenant_id, o.lexeme_uid;

    FOR t IN SELECT id FROM tenants LOOP
        PERFORM docray_refresh_entity_frequency_stats(t);
    END LOOP;
END;
$fn$;

REVOKE ALL ON FUNCTION docray_refresh_entity_frequency_stats(uuid) FROM PUBLIC;

-- =============================== attach RLS ================================
ALTER TABLE entity_frequency_stats ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_read ON entity_frequency_stats;
CREATE POLICY p_read ON entity_frequency_stats FOR SELECT
    USING (tenant_id = docray_current_tenant());

GRANT SELECT ON entity_frequency_stats TO docray_client;

-- =============================== backfill ==================================
SELECT docray_refresh_all_corpus_stats();

INSERT INTO schema_migrations (filename) VALUES ('100_read_model_token_entity.sql')
    ON CONFLICT (filename) DO NOTHING;
