-- doc-ray: corpus stat-band rollups — kill the 16s "The Corpus" landing query.
--
-- PROBLEM (measured as docray_api with an admin principal):
--   webapp GET /corpus recomputed four corpus-wide aggregates on EVERY page
--   load. Two of them scanned the whole 17.5M-row lexeme_occurrences table:
--     count(DISTINCT lexeme_uid)  -> 14.9 s   (index scan of all 17,559,177 rows)
--     count(*)                    ->  3.3 s   (seq scan of the same table)
--   Total ~18.7 s of origin time against a 300 ms SLA. The RLS annotation
--   semi-join filtered nothing (958 annotations, all visible) but still forced
--   a per-row filter that defeats an index-only count.
--
-- FIX — precompute, never scan on the read path:
--   document_stats  : per-document rollup of the SUMMABLE metrics (sentences,
--                     lexeme occurrences). RLS-scoped by document_id exactly like
--                     every other document table, so the landing page reads
--                     SUM(...) over only the caller's visible documents in ~2 ms
--                     and the numbers stay exact per principal (sensitive-doc and
--                     ACL scoping included).
--   corpus_stats    : the distinct-lexeme count, which is NOT summable across
--                     documents (a lexeme in N docs would be counted N times —
--                     measured: sum-of-per-doc-distinct = 1,092,256 vs. true
--                     235,365). It is therefore a maintained scalar, keyed PER
--                     TENANT (never one global row): a global scalar would leak
--                     one tenant's vocabulary magnitude to another the moment a
--                     second tenant is onboarded, and docray_readable_documents()
--                     is already tenant-scoped. The landing page gates it on
--                     "caller sees >= 1 document" so a principal with no visible
--                     corpus reads 0, preserving the confidentiality guarantee
--                     that tests/test_corpus_stats_rls.py was written to defend.
--
-- FRESHNESS: refreshes run in the trusted batch writer (the owner `docray`
--   connection that ingest/annotate use — it bypasses RLS by ownership), never
--   on the read path. document_stats is refreshed exactly per affected document;
--   the per-tenant distinct scalar is refreshed at most once per debounce window
--   (default 10 min) so a bulk re-annotation does not pay the ~13 s recompute
--   once per document. The scalar is therefore eventually consistent by design;
--   this migration backfills it exactly at deploy time.

-- =============================== rollup tables =============================

CREATE TABLE IF NOT EXISTS document_stats (
    document_id          uuid PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    n_sentences          bigint NOT NULL DEFAULT 0,
    n_lexeme_occurrences bigint NOT NULL DEFAULT 0,
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS corpus_stats (
    tenant_id          uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    n_distinct_lexemes bigint NOT NULL DEFAULT 0,
    computed_at        timestamptz NOT NULL DEFAULT now()
);

-- =============================== refresh helpers ===========================
-- SECURITY DEFINER (owned by the superuser/owner) so the recompute reads the
-- base tables WITHOUT RLS regardless of the caller's role, and search_path is
-- pinned against hijacking. Not granted to docray_client: only the trusted
-- batch writer (owner connection) mutates the rollups.

CREATE OR REPLACE FUNCTION docray_refresh_corpus_stats(tenant uuid) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS
$$
    INSERT INTO corpus_stats (tenant_id, n_distinct_lexemes, computed_at)
    SELECT tenant,
           count(DISTINCT lo.lexeme_uid),
           now()
      FROM documents d
      JOIN annotations a       ON a.document_id = d.id
      JOIN lexeme_occurrences lo ON lo.annotation_id = a.id
     WHERE d.tenant_id = tenant
    ON CONFLICT (tenant_id) DO UPDATE
        SET n_distinct_lexemes = EXCLUDED.n_distinct_lexemes,
            computed_at = now();
$$;

CREATE OR REPLACE FUNCTION docray_refresh_document_stats(doc uuid) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS
$$
    INSERT INTO document_stats (document_id, n_sentences, n_lexeme_occurrences, updated_at)
    VALUES (
        doc,
        (SELECT count(*) FROM sentences WHERE document_id = doc),
        (SELECT count(*)
           FROM lexeme_occurrences lo
           JOIN annotations a ON a.id = lo.annotation_id
          WHERE a.document_id = doc),
        now()
    )
    ON CONFLICT (document_id) DO UPDATE
        SET n_sentences = EXCLUDED.n_sentences,
            n_lexeme_occurrences = EXCLUDED.n_lexeme_occurrences,
            updated_at = now();
$$;

-- One entry point the pipeline calls after a document's counts change: refresh
-- that document's rollup exactly (cheap), then refresh its tenant's distinct
-- scalar only if the last recompute is older than `corpus_max_age` (debounce).
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
END;
$fn$;

CREATE OR REPLACE FUNCTION docray_refresh_all_corpus_stats() RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS
$$
    INSERT INTO document_stats (document_id, n_sentences, n_lexeme_occurrences, updated_at)
    SELECT d.id, coalesce(s.n, 0), coalesce(o.n, 0), now()
      FROM documents d
      LEFT JOIN (SELECT document_id, count(*) n FROM sentences GROUP BY document_id) s
             ON s.document_id = d.id
      LEFT JOIN (SELECT a.document_id, count(*) n
                   FROM lexeme_occurrences lo
                   JOIN annotations a ON a.id = lo.annotation_id
                  GROUP BY a.document_id) o
             ON o.document_id = d.id
    ON CONFLICT (document_id) DO UPDATE
        SET n_sentences = EXCLUDED.n_sentences,
            n_lexeme_occurrences = EXCLUDED.n_lexeme_occurrences,
            updated_at = now();

    INSERT INTO corpus_stats (tenant_id, n_distinct_lexemes, computed_at)
    SELECT d.tenant_id, count(DISTINCT lo.lexeme_uid), now()
      FROM documents d
      JOIN annotations a         ON a.document_id = d.id
      JOIN lexeme_occurrences lo ON lo.annotation_id = a.id
     GROUP BY d.tenant_id
    ON CONFLICT (tenant_id) DO UPDATE
        SET n_distinct_lexemes = EXCLUDED.n_distinct_lexemes,
            computed_at = now();
$$;

REVOKE ALL ON FUNCTION
    docray_refresh_corpus_stats(uuid),
    docray_refresh_document_stats(uuid),
    docray_refresh_stats_for_document(uuid, interval),
    docray_refresh_all_corpus_stats()
    FROM PUBLIC;

-- =============================== attach RLS ================================
-- document_stats inherits the standard document-scoped read policy (semi-join
-- vs. the caller's readable documents) so SUM(...) over it is per-principal
-- exact. corpus_stats has no document_id; it carries a bespoke tenant-scoped
-- read policy so a caller only ever sees their own tenant's row.

SELECT docray_attach_rls('document_stats'::regclass, 'document_id IN (%DOCS%)');

ALTER TABLE corpus_stats ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_read ON corpus_stats;
CREATE POLICY p_read ON corpus_stats FOR SELECT
    USING (tenant_id = docray_current_tenant());

GRANT SELECT ON document_stats, corpus_stats TO docray_client;

-- =============================== backfill ==================================
SELECT docray_refresh_all_corpus_stats();

INSERT INTO schema_migrations (filename) VALUES ('095_corpus_stats.sql')
    ON CONFLICT (filename) DO NOTHING;
