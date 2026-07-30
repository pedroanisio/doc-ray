-- doc-ray: make the tenant rollups incremental instead of full rebuilds.
--
-- PROBLEM, measured on the production cluster (2026-07-30, single tenant,
-- 786 documents). `docray_refresh_stats_for_document()` debounced on a
-- ten-minute window and then rebuilt both tenant rollups from scratch:
--
--     DELETE FROM lexeme_stats WHERE tenant_id = tenant;
--     INSERT INTO lexeme_stats ...
--       SELECT ... FROM lexeme_occurrences o JOIN annotations a ... GROUP BY ...
--
-- `lexeme_occurrences` holds 72,704,115 rows / 16 GB, so during ingest that
-- aggregate ran six times an hour; `docray_refresh_corpus_stats` added a second
-- full pass of the same table for `count(DISTINCT lo.lexeme_uid)`.
--
-- The write half was worse than the read half. Read directly from
-- `pg_stat_user_tables` while both rollups were being rebuilt on schedule:
--
--     table                      live rows    dead rows    dead %
--     entity_frequency_stats       633,294   56,394,549      98.9
--     lexeme_stats                 453,662   43,021,819      99.0
--
-- Autovacuum had run on both within the preceding five minutes and still could
-- not keep up: a full DELETE+INSERT of ~1.1M rows every ten minutes outruns it
-- permanently. Every reader then scanned ~9 GB of dead tuples to reach ~1M live
-- rows, and the rebuild plus the vacuum it provoked held CPU on an 8-vCPU
-- cluster sitting at a 5-9 load average.
--
-- FIX. A document's contribution to either rollup is separable and additive, so
-- the rollups become deltas rather than rebuilds:
--
--   * `document_lexeme_stats` / `document_entity_frequency_stats` snapshot what
--     each document last contributed.
--   * Refreshing a document counts only that document's rows (bounded by the
--     `annotations.document_id` index), diffs against its snapshot, and applies
--     the difference to the tenant rollup.
--
-- Cost per refresh drops from O(corpus) to O(document) — measured, the snapshot
-- for the whole corpus is 4,295,791 rows, ~5,600 distinct lexemes per document
-- against 72.7M occurrences scanned before. A key whose count did not move is
-- not written at all, so the dead-tuple flood stops at its source rather than
-- being chased by autovacuum.
--
-- Because the incremental path is cheap, the ten-minute debounce is also gone:
-- it existed only to amortise the rebuild, and its side effect was that readers
-- could see a rollup up to ten minutes stale.
--
-- The full-rebuild functions are kept as reconcilers (`docray_refresh_all_
-- corpus_stats`, and the per-tenant `docray_refresh_lexeme_stats` /
-- `docray_refresh_entity_frequency_stats`), rebuilt so the snapshots and the
-- tenant rollups are consistent by construction — the tenant table is literally
-- the sum of the snapshots. Derived state that is maintained by deltas needs a
-- way to be re-derived and checked; these are it.

-- =============================== schema ====================================

CREATE TABLE IF NOT EXISTS document_lexeme_stats (
    document_id uuid   NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    lexeme_uid  text   NOT NULL,
    uses        bigint NOT NULL,
    PRIMARY KEY (document_id, lexeme_uid)
);

CREATE TABLE IF NOT EXISTS document_entity_frequency_stats (
    document_id uuid   NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    entity_text text   NOT NULL,
    entity_type text   NOT NULL,
    uses        bigint NOT NULL,
    PRIMARY KEY (document_id, entity_text, entity_type)
);

COMMENT ON TABLE document_lexeme_stats IS
    'Per-document snapshot of what each document contributes to lexeme_stats. '
    'Internal maintenance state for the incremental rollup (migration 154); '
    'never a read path.';
COMMENT ON TABLE document_entity_frequency_stats IS
    'Per-document snapshot of what each document contributes to '
    'entity_frequency_stats. Internal maintenance state for the incremental '
    'rollup (migration 154); never a read path.';

-- A delta can drive a key to zero when the last document using it is
-- re-annotated or deleted. Those rows must go, or n_distinct_lexemes (a
-- count(*) over lexeme_stats as of this migration) would count dead keys.
-- Partial indexes keep that prune an index lookup instead of a table scan:
-- normally these indexes are empty.
CREATE INDEX IF NOT EXISTS idx_lexeme_stats_exhausted
    ON lexeme_stats (tenant_id) WHERE uses <= 0;
CREATE INDEX IF NOT EXISTS idx_entity_freq_exhausted
    ON entity_frequency_stats (tenant_id) WHERE uses <= 0;

-- =============================== incremental refresh =======================

CREATE OR REPLACE FUNCTION docray_refresh_document_lexeme_stats(doc uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS
$fn$
DECLARE
    t uuid;
BEGIN
    SELECT tenant_id INTO t FROM documents WHERE id = doc;
    IF t IS NULL THEN
        RETURN;
    END IF;

    -- 1. Apply (current - snapshotted) to the tenant rollup. Reads the OLD
    --    snapshot, so this must run before the snapshot is rewritten below.
    WITH fresh AS (
        SELECT o.lexeme_uid, count(*)::bigint AS uses
          FROM lexeme_occurrences o
          JOIN annotations a ON a.id = o.annotation_id
         WHERE a.document_id = doc
         GROUP BY o.lexeme_uid
    ),
    snap AS (
        SELECT lexeme_uid, uses FROM document_lexeme_stats WHERE document_id = doc
    ),
    delta AS (
        SELECT COALESCE(f.lexeme_uid, s.lexeme_uid) AS lexeme_uid,
               COALESCE(f.uses, 0) - COALESCE(s.uses, 0) AS diff
          FROM fresh f
          FULL JOIN snap s ON s.lexeme_uid = f.lexeme_uid
    )
    INSERT INTO lexeme_stats (tenant_id, lexeme_uid, uses)
    SELECT t, lexeme_uid, diff FROM delta WHERE diff <> 0
    ON CONFLICT (tenant_id, lexeme_uid) DO UPDATE
        SET uses = lexeme_stats.uses + EXCLUDED.uses;

    -- 2. Drop keys this tenant no longer uses (idx_lexeme_stats_exhausted).
    DELETE FROM lexeme_stats WHERE tenant_id = t AND uses <= 0;

    -- 3. Re-snapshot: forget keys the document no longer has, ...
    WITH fresh AS (
        SELECT o.lexeme_uid
          FROM lexeme_occurrences o
          JOIN annotations a ON a.id = o.annotation_id
         WHERE a.document_id = doc
         GROUP BY o.lexeme_uid
    )
    DELETE FROM document_lexeme_stats s
     WHERE s.document_id = doc
       AND NOT EXISTS (SELECT 1 FROM fresh f WHERE f.lexeme_uid = s.lexeme_uid);

    -- 4. ... and record the current counts. The WHERE on DO UPDATE keeps an
    --     unchanged key from producing a dead tuple.
    WITH fresh AS (
        SELECT o.lexeme_uid, count(*)::bigint AS uses
          FROM lexeme_occurrences o
          JOIN annotations a ON a.id = o.annotation_id
         WHERE a.document_id = doc
         GROUP BY o.lexeme_uid
    )
    INSERT INTO document_lexeme_stats (document_id, lexeme_uid, uses)
    SELECT doc, lexeme_uid, uses FROM fresh
    ON CONFLICT (document_id, lexeme_uid) DO UPDATE
        SET uses = EXCLUDED.uses
      WHERE document_lexeme_stats.uses IS DISTINCT FROM EXCLUDED.uses;
END;
$fn$;

CREATE OR REPLACE FUNCTION docray_refresh_document_entity_frequency_stats(doc uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS
$fn$
DECLARE
    t uuid;
BEGIN
    SELECT tenant_id INTO t FROM documents WHERE id = doc;
    IF t IS NULL THEN
        RETURN;
    END IF;

    WITH fresh AS (
        SELECT em.surface AS entity_text, em.entity_type, count(*)::bigint AS uses
          FROM entity_mention em
          JOIN annotations a ON a.id = em.annotation_id
         WHERE a.document_id = doc
         GROUP BY em.surface, em.entity_type
    ),
    snap AS (
        SELECT entity_text, entity_type, uses
          FROM document_entity_frequency_stats WHERE document_id = doc
    ),
    delta AS (
        SELECT COALESCE(f.entity_text, s.entity_text) AS entity_text,
               COALESCE(f.entity_type, s.entity_type) AS entity_type,
               COALESCE(f.uses, 0) - COALESCE(s.uses, 0) AS diff
          FROM fresh f
          FULL JOIN snap s
            ON s.entity_text = f.entity_text AND s.entity_type = f.entity_type
    )
    INSERT INTO entity_frequency_stats (tenant_id, entity_text, entity_type, uses)
    SELECT t, entity_text, entity_type, diff FROM delta WHERE diff <> 0
    ON CONFLICT (tenant_id, entity_text, entity_type) DO UPDATE
        SET uses = entity_frequency_stats.uses + EXCLUDED.uses;

    DELETE FROM entity_frequency_stats WHERE tenant_id = t AND uses <= 0;

    WITH fresh AS (
        SELECT em.surface AS entity_text, em.entity_type
          FROM entity_mention em
          JOIN annotations a ON a.id = em.annotation_id
         WHERE a.document_id = doc
         GROUP BY em.surface, em.entity_type
    )
    DELETE FROM document_entity_frequency_stats s
     WHERE s.document_id = doc
       AND NOT EXISTS (
           SELECT 1 FROM fresh f
            WHERE f.entity_text = s.entity_text AND f.entity_type = s.entity_type);

    WITH fresh AS (
        SELECT em.surface AS entity_text, em.entity_type, count(*)::bigint AS uses
          FROM entity_mention em
          JOIN annotations a ON a.id = em.annotation_id
         WHERE a.document_id = doc
         GROUP BY em.surface, em.entity_type
    )
    INSERT INTO document_entity_frequency_stats (document_id, entity_text, entity_type, uses)
    SELECT doc, entity_text, entity_type, uses FROM fresh
    ON CONFLICT (document_id, entity_text, entity_type) DO UPDATE
        SET uses = EXCLUDED.uses
      WHERE document_entity_frequency_stats.uses IS DISTINCT FROM EXCLUDED.uses;
END;
$fn$;

-- Deleting a document must withdraw its contribution, or the tenant rollup keeps
-- counting rows that no longer exist. Before 154 the next full rebuild papered
-- over this; with deltas there is no rebuild to rely on.
CREATE OR REPLACE FUNCTION docray_unapply_document_rollups(doc uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS
$fn$
DECLARE
    t uuid;
BEGIN
    SELECT tenant_id INTO t FROM documents WHERE id = doc;
    IF t IS NULL THEN
        RETURN;
    END IF;

    INSERT INTO lexeme_stats (tenant_id, lexeme_uid, uses)
    SELECT t, s.lexeme_uid, -s.uses
      FROM document_lexeme_stats s WHERE s.document_id = doc
    ON CONFLICT (tenant_id, lexeme_uid) DO UPDATE
        SET uses = lexeme_stats.uses + EXCLUDED.uses;
    DELETE FROM lexeme_stats WHERE tenant_id = t AND uses <= 0;
    DELETE FROM document_lexeme_stats WHERE document_id = doc;

    INSERT INTO entity_frequency_stats (tenant_id, entity_text, entity_type, uses)
    SELECT t, s.entity_text, s.entity_type, -s.uses
      FROM document_entity_frequency_stats s WHERE s.document_id = doc
    ON CONFLICT (tenant_id, entity_text, entity_type) DO UPDATE
        SET uses = entity_frequency_stats.uses + EXCLUDED.uses;
    DELETE FROM entity_frequency_stats WHERE tenant_id = t AND uses <= 0;
    DELETE FROM document_entity_frequency_stats WHERE document_id = doc;
END;
$fn$;

CREATE OR REPLACE FUNCTION docray_documents_unapply_rollups() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS
$fn$
BEGIN
    PERFORM docray_unapply_document_rollups(OLD.id);
    RETURN OLD;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_documents_unapply_rollups ON documents;
CREATE TRIGGER trg_documents_unapply_rollups
    BEFORE DELETE ON documents
    FOR EACH ROW EXECUTE FUNCTION docray_documents_unapply_rollups();

-- =============================== reconcilers ===============================

-- Kept as the re-derivation path, not the hot path. Rebuilt so the snapshot and
-- the tenant rollup come from one pass and cannot disagree: the tenant table is
-- the sum of the per-document snapshots by construction.
CREATE OR REPLACE FUNCTION docray_refresh_lexeme_stats(tenant uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS
$fn$
BEGIN
    DELETE FROM document_lexeme_stats s
     USING documents d
     WHERE d.id = s.document_id AND d.tenant_id = tenant;

    INSERT INTO document_lexeme_stats (document_id, lexeme_uid, uses)
    SELECT a.document_id, o.lexeme_uid, count(*)
      FROM lexeme_occurrences o
      JOIN annotations a ON a.id = o.annotation_id
      JOIN documents d   ON d.id = a.document_id
     WHERE d.tenant_id = tenant
     GROUP BY a.document_id, o.lexeme_uid;

    DELETE FROM lexeme_stats WHERE tenant_id = tenant;

    INSERT INTO lexeme_stats (tenant_id, lexeme_uid, uses)
    SELECT tenant, s.lexeme_uid, sum(s.uses)
      FROM document_lexeme_stats s
      JOIN documents d ON d.id = s.document_id
     WHERE d.tenant_id = tenant
     GROUP BY s.lexeme_uid;
END;
$fn$;

CREATE OR REPLACE FUNCTION docray_refresh_entity_frequency_stats(tenant uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS
$fn$
BEGIN
    DELETE FROM document_entity_frequency_stats s
     USING documents d
     WHERE d.id = s.document_id AND d.tenant_id = tenant;

    INSERT INTO document_entity_frequency_stats (document_id, entity_text, entity_type, uses)
    SELECT a.document_id, em.surface, em.entity_type, count(*)
      FROM entity_mention em
      JOIN annotations a ON a.id = em.annotation_id
      JOIN documents d   ON d.id = a.document_id
     WHERE d.tenant_id = tenant
     GROUP BY a.document_id, em.surface, em.entity_type;

    DELETE FROM entity_frequency_stats WHERE tenant_id = tenant;

    INSERT INTO entity_frequency_stats (tenant_id, entity_text, entity_type, uses)
    SELECT tenant, s.entity_text, s.entity_type, sum(s.uses)
      FROM document_entity_frequency_stats s
      JOIN documents d ON d.id = s.document_id
     WHERE d.tenant_id = tenant
     GROUP BY s.entity_text, s.entity_type;
END;
$fn$;

-- `n_distinct_lexemes` was a `count(DISTINCT lo.lexeme_uid)` over the 72.7M-row
-- lexeme_occurrences table — the second full pass this migration removes. With
-- lexeme_stats holding exactly one row per lexeme the tenant still uses (rows
-- reaching zero are pruned above), the distinct count is that table's row count
-- and the denominator from migration 152 is its SUM. Both are index-bounded.
CREATE OR REPLACE FUNCTION docray_refresh_corpus_stats(tenant uuid) RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS
$$
    INSERT INTO corpus_stats (
        tenant_id, n_distinct_lexemes, n_sentences, total_lexeme_uses, computed_at
    )
    SELECT tenant,
           (SELECT count(*) FROM lexeme_stats ls WHERE ls.tenant_id = tenant),
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
$$;

-- =============================== pipeline entry point ======================

CREATE OR REPLACE FUNCTION docray_refresh_stats_for_document(
    doc uuid,
    corpus_max_age interval DEFAULT interval '10 minutes'
) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS
$fn$
DECLARE
    t uuid;
BEGIN
    PERFORM docray_refresh_document_stats(doc);
    SELECT tenant_id INTO t FROM documents WHERE id = doc;
    IF t IS NULL THEN
        RETURN;
    END IF;

    -- `corpus_max_age` is accepted for signature compatibility and deliberately
    -- ignored. It gated a full-corpus rebuild; the refreshes below are bounded
    -- by one document, so there is nothing left to amortise and no reason to
    -- serve a reader a rollup up to ten minutes stale. Callers that passed 0 to
    -- force a refresh (application/document_stats.py) now get that behaviour
    -- unconditionally.
    PERFORM docray_refresh_document_lexeme_stats(doc);
    PERFORM docray_refresh_document_entity_frequency_stats(doc);

    -- Strictly after the rollups it summarises. The pre-154 order refreshed
    -- corpus_stats first, so migration 152's total_lexeme_uses — a SUM over
    -- lexeme_stats — was stamped from the previous cycle's numbers.
    PERFORM docray_refresh_corpus_stats(t);
END;
$fn$;

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

    -- corpus_stats now derives from lexeme_stats, so the rollups must be
    -- rebuilt before it is stamped.
    FOR t IN SELECT id FROM tenants LOOP
        PERFORM docray_refresh_lexeme_stats(t);
        PERFORM docray_refresh_entity_frequency_stats(t);
        PERFORM docray_refresh_corpus_stats(t);
    END LOOP;
END;
$fn$;

REVOKE ALL ON FUNCTION
    docray_refresh_document_lexeme_stats(uuid),
    docray_refresh_document_entity_frequency_stats(uuid),
    docray_unapply_document_rollups(uuid)
    FROM PUBLIC;

-- =============================== attach RLS ================================

-- Internal maintenance state: the SECURITY DEFINER functions above are the only
-- readers, and they run as docray_batch (BYPASSRLS). RLS is enabled with no
-- SELECT policy and no grant to docray_client, so a client role cannot reach
-- per-document counts for documents it may not read.
ALTER TABLE document_lexeme_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_entity_frequency_stats ENABLE ROW LEVEL SECURITY;

-- =============================== backfill ==================================

-- Seeds both snapshots and re-derives the tenant rollups from them in one pass,
-- which is also the cutover: from here the deltas have a baseline to diff
-- against. Expect this to cost roughly one pre-154 refresh cycle plus the
-- ~4.3M snapshot inserts.
SELECT docray_refresh_all_corpus_stats();

INSERT INTO schema_migrations (filename)
VALUES ('154_incremental_corpus_rollups.sql')
ON CONFLICT (filename) DO NOTHING;
