-- doc-ray: repair the corpus sentence denominator full-refresh path (#42).
--
-- ROOT CAUSE (confirmed by read-only diagnosis of a deployed corpus): migration
-- 142 added corpus_stats.n_sentences to docray_refresh_corpus_stats(uuid), then
-- attempted its deploy-time backfill by calling docray_refresh_all_corpus_stats().
-- The full-refresh function was still migration 100's definition and never wrote
-- n_sentences, so an existing corpus kept the column's DEFAULT 0. Later per-document
-- refreshes happened to self-repair that corpus; a fresh 142 replay or a new tenant
-- reached only through the full refresh would reproduce the impossible 43-of-0
-- term-frequency response.
--
-- The leading ownership hypothesis was false: both helpers are
-- already SECURITY DEFINER functions owned by docray_batch (BYPASSRLS), and no
-- vulnerable forced-RLS helper remains. The ownership assertion below stays as a
-- loud invariant because a future CREATE OR REPLACE can otherwise regress it.

-- Full rebuild: migration 100's complete read-model refresh, with corpus_stats
-- now rebuilt from two maintained inputs in one tenant-grain upsert. The read path
-- still performs no fact-table aggregate; this work runs only at the batch/migration
-- boundary under the trusted writer.
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
               row_number() OVER (
                   PARTITION BY a.document_id ORDER BY count(*) DESC, l.lemma
               ) AS rn
          FROM lexeme_occurrences o
          JOIN annotations a ON a.id = o.annotation_id
          JOIN lexemes l     ON l.uid = o.lexeme_uid
         WHERE l.pos = 'VERB'
         GROUP BY a.document_id, l.uid, l.lemma
    ),
    verbs_json AS (
        SELECT document_id,
               jsonb_agg(
                   jsonb_build_object('uid', uid, 'lemma', lemma, 'n', n) ORDER BY rn
               ) AS j
          FROM verbs_ranked WHERE rn <= 15 GROUP BY document_id
    )
    INSERT INTO document_stats (
        document_id, n_sentences, n_lexeme_occurrences, n_tokens,
        pos_counts, top_verbs, updated_at
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

    WITH lexeme_counts AS (
        SELECT d.tenant_id, count(DISTINCT o.lexeme_uid)::bigint AS n
          FROM documents d
          JOIN annotations a ON a.document_id = d.id
          JOIN lexeme_occurrences o ON o.annotation_id = a.id
         GROUP BY d.tenant_id
    ),
    sentence_counts AS (
        SELECT d.tenant_id, COALESCE(SUM(ds.n_sentences), 0)::bigint AS n
          FROM documents d
          LEFT JOIN document_stats ds ON ds.document_id = d.id
         GROUP BY d.tenant_id
    )
    INSERT INTO corpus_stats (
        tenant_id, n_distinct_lexemes, n_sentences, computed_at
    )
    SELECT tenant.id,
           COALESCE(lexeme_counts.n, 0),
           COALESCE(sentence_counts.n, 0),
           now()
      FROM tenants tenant
      LEFT JOIN lexeme_counts ON lexeme_counts.tenant_id = tenant.id
      LEFT JOIN sentence_counts ON sentence_counts.tenant_id = tenant.id
    ON CONFLICT (tenant_id) DO UPDATE
        SET n_distinct_lexemes = EXCLUDED.n_distinct_lexemes,
            n_sentences = EXCLUDED.n_sentences,
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

REVOKE ALL ON FUNCTION
    docray_refresh_corpus_stats(uuid),
    docray_refresh_all_corpus_stats()
    FROM PUBLIC;

-- Re-assert the batch-owner invariant. Role-absent minimal development databases
-- retain their migration owner; every production/test database creates docray_batch
-- in migration 106. A failed reassignment is checked loudly by the next block.
DO $$
BEGIN
    IF to_regrole('docray_batch') IS NULL THEN
        RETURN;
    END IF;
    BEGIN
        ALTER FUNCTION docray_refresh_corpus_stats(uuid) OWNER TO docray_batch;
        ALTER FUNCTION docray_refresh_all_corpus_stats() OWNER TO docray_batch;
    EXCEPTION
        WHEN insufficient_privilege THEN NULL;
    END;
END
$$;

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
               'docray_refresh_corpus_stats(uuid)'::regprocedure,
               'docray_refresh_all_corpus_stats()'::regprocedure
           )
       AND (r.rolname <> 'docray_batch' OR NOT r.rolbypassrls OR NOT p.prosecdef);

    IF invalid_helpers IS NOT NULL THEN
        RAISE EXCEPTION
            'corpus rollup helpers violate the batch-owner/RLS invariant: %',
            invalid_helpers;
    END IF;
END
$$;

-- Repair every existing tenant now. Unlike migration 142's attempted backfill,
-- this call reaches the corrected full-refresh definition above.
SELECT docray_refresh_all_corpus_stats();

INSERT INTO schema_migrations (filename)
VALUES ('146_corpus_stats_sentence_denominator.sql')
ON CONFLICT (filename) DO NOTHING;
