-- Block F (P3) — Hyper-context rollups: per-lens entity landscape + co-occurrence
-- relationship discovery, migration 133.
--
-- After Blocks B-D the corpus knows who each mention is within a lens, but answering
-- "who are the significant figures in this body of work, and how do they relate?" by
-- scanning mentions at read time is impossible at 10k documents — the live-aggregate-
-- over-a-fact-table pattern D-024 forbids. So the capability *is* the precomputation:
-- two derived rollups, refreshed at the pipeline boundary (strictly AFTER Block D's
-- projection refresh, which they aggregate), read cheaply. See
-- docs/proposals/entity-fabric-substrate-lens-projection.md (§4 hyper-context, §5).
--
-- Recorded open decisions (issue §9):
--   D-1  Co-occurrence unit = SENTENCE (precise; sentences carry ordinals for citation).
--        Two entities co-occur when their projected mentions fall in the same sentence.
--   D-2  Strength = NPMI (Bouma 2009), normalized by marginal sentence frequencies, so a
--        ubiquitous entity is not "strongly related" to everything. Formula + range +
--        degenerate cases are in doc_ray.domain.lens_rollups.npmi (raw counts alone are
--        not acceptable). strength ∈ [-1, 1]; only co-occurring pairs (joint > 0) stored.
--   D-3  Narrative summaries (FR-2) are DEFERRED to a follow-up (BL-161): they add a model
--        dependency + review workflow deserving their own verification gate. FR-1/3/4/5/7
--        (the deterministic rollups + relationship discovery) ship here, complete.
--   D-4  No graph clustering / community detection (speculative without real query
--        patterns) — rollups + bounded traversal only.
--
-- SUPERSESSION. Block D (#8) shipped lens_entity_stats (a thin per-lens count rollup) as
-- the backing for `lensEntities`. lens_entity_rollup below is a strict superset (adds the
-- type profile, type_consistency, first_seen_ordinal for citation) and becomes the single
-- backing for `lensEntities`, so lens_entity_stats is dropped — one rollup for one thing,
-- no duplication. The Block D projection refresh now chains this rollup refresh in its
-- place.

DROP TABLE IF EXISTS lens_entity_stats CASCADE;

-- ==========================================================================
-- FR-1 — lens_entity_rollup: the significant-entities-in-a-lens rollup.
-- ==========================================================================
CREATE TABLE IF NOT EXISTS lens_entity_rollup (
    lens_id            uuid   NOT NULL REFERENCES document_collections(id) ON DELETE CASCADE,
    canonical_id       bigint NOT NULL REFERENCES canonical(id) ON DELETE CASCADE,
    scope              text   NOT NULL CHECK (scope IN ('local', 'global')),
    mention_count      int    NOT NULL,
    document_count     int    NOT NULL,
    type_profile       jsonb  NOT NULL,   -- {"PERSON": 120, "GPE": 8} — observed spread
    type_consistency   real,             -- dominant share (domain.entity_profiles)
    first_seen_ordinal int,              -- earliest sentence ordinal, for citation
    refreshed_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lens_id, canonical_id)
);
-- "top entities in a lens" ranked by volume (the lensEntities read path).
CREATE INDEX IF NOT EXISTS idx_lens_entity_rollup_rank
    ON lens_entity_rollup (lens_id, mention_count DESC, canonical_id);

-- ==========================================================================
-- FR-1 — lens_entity_relation: undirected co-occurrence pairs within a lens.
-- CHECK (from < to) stores each undirected pair ONCE; the read layer therefore
-- queries BOTH orderings (WHERE from = x OR to = x) to find an entity's relations.
-- ==========================================================================
CREATE TABLE IF NOT EXISTS lens_entity_relation (
    lens_id            uuid   NOT NULL REFERENCES document_collections(id) ON DELETE CASCADE,
    from_canonical_id  bigint NOT NULL REFERENCES canonical(id) ON DELETE CASCADE,
    to_canonical_id    bigint NOT NULL REFERENCES canonical(id) ON DELETE CASCADE,
    cooccurrence_count int    NOT NULL,
    strength           real   NOT NULL,   -- NPMI (Bouma 2009); see migration header / domain
    refreshed_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lens_id, from_canonical_id, to_canonical_id),
    CHECK (from_canonical_id < to_canonical_id)
);
-- "strongest relations for an entity", either ordering of the stored pair.
CREATE INDEX IF NOT EXISTS idx_lens_relation_from
    ON lens_entity_relation (lens_id, from_canonical_id, strength DESC);
CREATE INDEX IF NOT EXISTS idx_lens_relation_to
    ON lens_entity_relation (lens_id, to_canonical_id, strength DESC);

-- ==========================================================================
-- RLS. Neither table has a document_id, so the predicate scopes by LENS visibility:
-- a caller may read a lens's rollups iff they can read >= 1 of the lens's member
-- documents (the same reasoning Block D/E used — a lens's aggregate landscape must
-- not leak to someone who cannot see any of its documents). FORCE so even the owner
-- is filtered; the batch writer is docray_batch (BYPASSRLS, migration 106). Writes
-- are admin-only at the policy level; the derived rollups are (re)built by the batch
-- refresh, never by an API request.
-- ==========================================================================
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['lens_entity_rollup', 'lens_entity_relation'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS %I_read ON %I', t, t);
        EXECUTE format(
            'CREATE POLICY %I_read ON %I FOR SELECT USING ('
            '  docray_is_admin() OR lens_id IN ('
            '    SELECT collection_id FROM document_collection_members'
            '    WHERE document_id IN (SELECT * FROM docray_readable_documents())))', t, t);
        EXECUTE format('DROP POLICY IF EXISTS %I_write ON %I', t, t);
        EXECUTE format(
            'CREATE POLICY %I_write ON %I FOR ALL '
            'USING (docray_is_admin()) WITH CHECK (docray_is_admin())', t, t);
        EXECUTE format('GRANT SELECT ON %I TO docray_api', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO docray_batch', t);
    END LOOP;
END $$;

INSERT INTO schema_migrations (filename) VALUES ('133_lens_rollups.sql')
    ON CONFLICT (filename) DO NOTHING;
