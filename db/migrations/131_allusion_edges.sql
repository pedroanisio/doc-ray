-- Block E (P3) — Allusion edges: typed cross-lens links, migration 131.
--
-- An allusion is an EDGE, never an equality (issue #9; proposal §3.3). Block D (#8)
-- keeps a novel's Paris and the real Paris as distinct entities so nothing is
-- fabricated by a merge (invariant 2); this table records the honest relationship
-- between them — the novel's Paris *alludes to* the real Paris — as a typed,
-- directional link that preserves both and makes the connection queryable. Recording
-- an edge changes NOTHING about projection, resolution, or the canonical registry:
-- it is additive curation metadata (see doc_ray.domain.allusions for the pure
-- vocabulary and the negative invariants — no identity, no transitivity).
--
-- Recorded open decisions (issue §9):
--   D-1  Endpoints are (canonical_id, lens_id), NOT entity_projection rows. Projections
--        are rebuilt on re-annotation; anchoring a curated edge to one would make the
--        curation vanish on the next annotate — the Block A failure mode. canonical_id +
--        lens_id are stable.
--   D-2  RLS (the hard part). An edge spans two lenses of possibly different visibility,
--        and canonical is tenant-global with no document_id. The read predicate below is
--        conservative: the caller must be able to see BOTH endpoints' lenses AND (when
--        present) the evidence document. A lens is "seen" if it is the public `global`
--        lens OR the caller can read >= 1 of its member documents. This is proven by an
--        asymmetry test (a caller who reads only one side sees zero rows,
--        test_allusion_edges_db.py). The `global` lens has no membership rows (it is the
--        shared identity scope, not a container), so it is treated as universally
--        visible — otherwise no edge to a real-world entity would ever be readable.
--   D-3  Truncate classification: WIPED. entity_allusion references document_collections
--        (the lenses), which truncate_corpus WIPES; an edge to/from a wiped lens is
--        meaningless, and classifying it preserved would both break the FK-consistency
--        gate and leave dangling lens references. So it is corpus-derived curation,
--        wiped with the corpus (test_truncate_corpus.py stays green honestly).
--   D-4  Edge-type vocabulary: the five specified relations. Adding one later is a
--        migration (the CHECK is deliberate) — justified from real corpus need, not
--        speculation.
--
-- This is a CURATION table (contrast Block D's derived fact tables): the API writes it
-- directly (docray_client INSERT/UPDATE), because a human/agent drawing an allusion is
-- a review decision recorded on a table it may write, not a derived write that must go
-- through the batch worker. Machine-proposed edges still land status='proposed'.

CREATE TABLE IF NOT EXISTS entity_allusion (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_canonical_id    bigint NOT NULL REFERENCES canonical(id) ON DELETE CASCADE,
    from_lens_id         uuid   NOT NULL REFERENCES document_collections(id) ON DELETE CASCADE,
    to_canonical_id      bigint NOT NULL REFERENCES canonical(id) ON DELETE CASCADE,
    to_lens_id           uuid   NOT NULL REFERENCES document_collections(id) ON DELETE CASCADE,
    edge_type            text NOT NULL CHECK (edge_type IN
                           ('alludes_to', 'depicts', 'references', 'adapted_from', 'inspired_by')),
    confidence           real CHECK (confidence >= 0 AND confidence <= 1),
    method               text NOT NULL,       -- 'human' | 'heuristic:<v>' | 'model:<name>'
    status               text NOT NULL DEFAULT 'proposed'
                           CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    reviewer             uuid REFERENCES principals(id) ON DELETE SET NULL,
    rationale            text,
    evidence_document_id uuid REFERENCES documents(id) ON DELETE SET NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    -- Idempotent edge creation: one edge per (from, to, type).
    UNIQUE (from_canonical_id, from_lens_id, to_canonical_id, to_lens_id, edge_type),
    -- No self-edge within one lens (meaningless); mirrors domain.allusions.is_self_edge.
    CHECK (from_canonical_id <> to_canonical_id OR from_lens_id <> to_lens_id)
);

-- Bidirectional traversal: "what does this entity allude to" (from) and "what alludes
-- to this entity" (to). Both indexed so neither direction scans.
CREATE INDEX IF NOT EXISTS idx_entity_allusion_from
    ON entity_allusion (from_canonical_id, from_lens_id);
CREATE INDEX IF NOT EXISTS idx_entity_allusion_to
    ON entity_allusion (to_canonical_id, to_lens_id);
CREATE INDEX IF NOT EXISTS idx_entity_allusion_evidence
    ON entity_allusion (evidence_document_id) WHERE evidence_document_id IS NOT NULL;

-- --------------------------------------------------------------------------
-- RLS (D-2). FORCE so even the table owner is filtered; the batch writer is
-- docray_batch (BYPASSRLS, migration 106). The predicate never reveals the
-- existence of an entity in a lens the caller cannot see.
-- --------------------------------------------------------------------------
ALTER TABLE entity_allusion ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_allusion FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS entity_allusion_read ON entity_allusion;
CREATE POLICY entity_allusion_read ON entity_allusion
    FOR SELECT
    USING (
        docray_is_admin()
        OR (
            -- both endpoint lenses visible (global is the public shared scope; a scope
            -- lens is visible only if the caller reads >= 1 of its member documents)…
            (from_lens_id = (SELECT id FROM document_collections WHERE name = 'global')
             OR from_lens_id IN (
                 SELECT collection_id FROM document_collection_members
                 WHERE document_id IN (SELECT * FROM docray_readable_documents())))
            AND (to_lens_id = (SELECT id FROM document_collections WHERE name = 'global')
                 OR to_lens_id IN (
                     SELECT collection_id FROM document_collection_members
                     WHERE document_id IN (SELECT * FROM docray_readable_documents())))
            -- …and the justifying document, when present, is itself readable.
            AND (evidence_document_id IS NULL
                 OR evidence_document_id IN (SELECT * FROM docray_readable_documents()))
        )
    );

-- Writes are anchored to the evidence document the caller is curating: they may only
-- create/confirm/reject an edge justified by a document they can write. (If the
-- evidence document is later deleted, evidence_document_id becomes NULL and only an
-- admin may further edit the edge — its source justification is gone.)
--
-- CRITICAL: the write policy is FOR INSERT/UPDATE only, NOT `FOR ALL`. A `FOR ALL`
-- policy also governs SELECT, and permissive policies are OR-ed — so a `FOR ALL`
-- write policy would let anyone who can write the evidence document READ the edge,
-- bypassing the two-lens read predicate above and leaking the far lens's entity. The
-- read side must be governed by entity_allusion_read ALONE (the same reason
-- docray_attach_rls splits p_ins/p_upd/p_del). The asymmetry test in
-- test_allusion_edges_db.py fails if this is ever collapsed back to FOR ALL.
DROP POLICY IF EXISTS entity_allusion_write ON entity_allusion;  -- legacy FOR ALL name
DROP POLICY IF EXISTS entity_allusion_insert ON entity_allusion;
CREATE POLICY entity_allusion_insert ON entity_allusion
    FOR INSERT
    WITH CHECK (
        docray_is_admin()
        OR (evidence_document_id IS NOT NULL
            AND evidence_document_id IN (SELECT * FROM docray_writable_documents()))
    );
DROP POLICY IF EXISTS entity_allusion_update ON entity_allusion;
CREATE POLICY entity_allusion_update ON entity_allusion
    FOR UPDATE
    USING (
        docray_is_admin()
        OR (evidence_document_id IS NOT NULL
            AND evidence_document_id IN (SELECT * FROM docray_writable_documents()))
    )
    WITH CHECK (
        docray_is_admin()
        OR (evidence_document_id IS NOT NULL
            AND evidence_document_id IN (SELECT * FROM docray_writable_documents()))
    );

-- CURATION table (D-3): the API curates it directly (docray_api inherits docray_client,
-- migration 030), the batch pipeline may also write (heuristic proposer). No DELETE —
-- a rejection is a status change; row cleanup rides FK CASCADE from canonical/collections.
GRANT SELECT ON entity_allusion TO docray_api;
GRANT SELECT, INSERT, UPDATE ON entity_allusion TO docray_client, docray_batch;

INSERT INTO schema_migrations (filename) VALUES ('131_allusion_edges.sql')
    ON CONFLICT (filename) DO NOTHING;
