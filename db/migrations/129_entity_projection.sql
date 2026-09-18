-- Projection Engine: lens-scoped entity identity, migration 129.
--
-- The heart of the Substrate·Lens·Projection architecture (issue #8; proposal
-- docs/design/entity-fabric.md §3.3). Identity stops
-- being a property baked into the substrate and becomes a DERIVED, refreshable
-- interpretation: entity = project(mention, lens). The mention is the immutable
-- fact; who it *is* depends on the lens. The same corpus then holds fiction and
-- non-fiction side by side — a fictional "Duncan" stays inside its work lens while
-- the real "Paris" is shared across every history — with no lossy merge, because a
-- projection is derived (drop and rebuild) where a merge is destructive.
--
-- This migration is ADDITIVE and never rewrites the substrate (invariant 1/2):
-- entity_mention / spans / canonical are untouched. It adds four derived tables and
-- one designation column, all reproducible from substrate + lens + policy.
--
-- Recorded open decisions (issue §10 — the agent must choose and record):
--   D-1  Plain tables + a refresh job (NOT a MATERIALIZED VIEW): a view cannot carry
--        per-row review status (proposed/confirmed/rejected), which FR-6 requires.
--   D-2  Incremental refresh per (document, lens) chained after canonicalize /
--        reannotate / apply_corrections; an operator-invoked full rebuild for policy
--        changes. Staleness window: a projection is stale only between a substrate
--        change and the chained refresh (same request for a single-doc pipeline).
--   D-3  Human confirm/reject is a SEPARATE proposal table (entity_projection_review)
--        the API may write (docray_client), applied by the batch refresh — the NER-correction log's
--        record-then-apply shape. entity_projection stays batch-owned (FR-1), so the
--        API never writes the derived fact table in a request. The review is keyed by
--        the STABLE span identity (document_id, char_start, char_end, lens_id), not by
--        the ephemeral mention_id, so a human decision SURVIVES re-annotation and is
--        re-applied to the fresh mentions (migration 115).
--   D-4  A document in several projecting lenses with no default lens falls back to
--        `global` for its default read; the read result records the ambiguity rather
--        than picking a scope lens arbitrarily (see application/projection.py).
--   D-5  Not applicable — genre classification (issue #6) is merged; the refresh consumes
--        doc_ray.domain.genre_policy.resolution_scope directly (no stub).
--
-- Local identity minting (why lens_local_entity exists): entity_projection.canonical_id
-- must reference canonical(id) (FR-1), so a work-local "Duncan" is a canonical row with
-- wikidata_id NULL (the "local-only entity" the 021 schema already anticipated). To
-- make a rebuild REPRODUCIBLE (invariant 3 — identical rows), minting must reuse the
-- same canonical row across refreshes: lens_local_entity is the stable registry keyed
-- (lens_id, surface_norm, entity_type) -> canonical_id. Kept OUT of `canonical` itself
-- so the global registry's schema and its readers are untouched.

-- ==========================================================================
-- FR-2 — Designation. Only an explicitly PROJECTING lens is materialized (cost
-- control: projecting every lens for every mention at 10k documents is not
-- viable). A projecting collection must itself be a lens (is_scope); enforced by
-- a declarative row CHECK — no trigger needed, and it composes with the lens registry
-- lens guard (migration 121) untouched.
-- ==========================================================================

ALTER TABLE document_collections
    ADD COLUMN IF NOT EXISTS projects boolean NOT NULL DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_collection_projects_requires_scope'
    ) THEN
        ALTER TABLE document_collections
            ADD CONSTRAINT chk_collection_projects_requires_scope
            CHECK (projects IS FALSE OR is_scope IS TRUE);
    END IF;
END $$;

-- Find a document's projecting lenses cheaply (per-document read path).
CREATE INDEX IF NOT EXISTS idx_document_collections_projects
    ON document_collections (id) WHERE projects;

-- The reserved `global` lens always projects: real-world identity is the fallback
-- every document resolves against (invariant 4).
UPDATE document_collections SET projects = true WHERE name = 'global' AND NOT projects;

-- ==========================================================================
-- FR-1 — entity_projection: the derived interpretation. One row per (mention,
-- projecting lens). canonical_id NULL = explicitly unresolved (never dropped,
-- invariant 4). document_id is DENORMALIZED on purpose: the RLS predicate is then a
-- direct semi-join to docray_readable_documents() instead of a two-hop join through
-- annotations on every row check (a per-row performance decision, not redundancy —
-- cf. resolution_review's slower two-hop policy in migration 123). It is kept correct
-- by the writer (application/projection.py always derives it from the mention's
-- document); there is exactly one writer.
-- ==========================================================================

CREATE TABLE IF NOT EXISTS entity_projection (
    mention_id   ulid NOT NULL REFERENCES entity_mention(id) ON DELETE CASCADE,
    lens_id      uuid NOT NULL REFERENCES document_collections(id) ON DELETE CASCADE,
    document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    canonical_id bigint REFERENCES canonical(id) ON DELETE SET NULL,   -- NULL = unresolved
    scope        text NOT NULL CHECK (scope IN ('local', 'global')),
    confidence   real CHECK (confidence >= 0 AND confidence <= 1),
    method       text NOT NULL,        -- 'exact_form' | 'unresolved' | 'embedding' | 'human'
    status       text NOT NULL DEFAULT 'proposed'
                 CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    refreshed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (mention_id, lens_id)
);

-- "all mentions of this entity in this lens" and "this document's projection in a lens".
CREATE INDEX IF NOT EXISTS idx_entity_projection_lens_canonical
    ON entity_projection (lens_id, canonical_id);
CREATE INDEX IF NOT EXISTS idx_entity_projection_document_lens
    ON entity_projection (document_id, lens_id);

-- RLS: a projection is readable/writable exactly when its document is. Direct on the
-- denormalized document_id (fast). FORCE so even the table owner is filtered; the
-- batch writer is docray_batch (BYPASSRLS, migration 106), so the refresh is exempt.
ALTER TABLE entity_projection ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_projection FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS entity_projection_read ON entity_projection;
CREATE POLICY entity_projection_read ON entity_projection
    FOR SELECT
    USING (docray_is_admin()
           OR document_id IN (SELECT * FROM docray_readable_documents()));

DROP POLICY IF EXISTS entity_projection_write ON entity_projection;
CREATE POLICY entity_projection_write ON entity_projection
    FOR ALL
    USING (docray_is_admin()
           OR document_id IN (SELECT * FROM docray_writable_documents()))
    WITH CHECK (docray_is_admin()
                OR document_id IN (SELECT * FROM docray_writable_documents()));

-- Derived fact table: the API/webapp only READ it; the batch worker owns the writes
-- (the API must never write a derived fact table in a request).
GRANT SELECT ON entity_projection TO docray_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON entity_projection TO docray_batch;

-- ==========================================================================
-- lens_local_entity: the stable local-identity registry that makes minting
-- reproducible (see header). Not per-document — per (lens, surface, type). Only the
-- batch refresh (docray_batch, BYPASSRLS) reads or writes it, so RLS is enabled with
-- an admin-only policy (it never widens visibility; no api/client grant exists).
-- ==========================================================================

CREATE TABLE IF NOT EXISTS lens_local_entity (
    lens_id      uuid NOT NULL REFERENCES document_collections(id) ON DELETE CASCADE,
    surface_norm text NOT NULL,   -- casefolded, whitespace-collapsed (domain.projection.local_key)
    entity_type  text NOT NULL,   -- upper-cased
    canonical_id bigint NOT NULL REFERENCES canonical(id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lens_id, surface_norm, entity_type)
);
CREATE INDEX IF NOT EXISTS idx_lens_local_entity_canonical
    ON lens_local_entity (canonical_id);

ALTER TABLE lens_local_entity ENABLE ROW LEVEL SECURITY;
ALTER TABLE lens_local_entity FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS lens_local_entity_admin ON lens_local_entity;
CREATE POLICY lens_local_entity_admin ON lens_local_entity
    FOR ALL USING (docray_is_admin()) WITH CHECK (docray_is_admin());

GRANT SELECT, INSERT, UPDATE, DELETE ON lens_local_entity TO docray_batch;

-- ==========================================================================
-- FR-6 / D-3 — entity_projection_review: the human confirm/reject proposal the API
-- may write; the batch refresh applies it onto entity_projection.status. Keyed by
-- the STABLE span identity (like the NER-correction log, migration 115) so a
-- decision survives re-annotation and is re-applied to the fresh mentions.
-- ==========================================================================

CREATE TABLE IF NOT EXISTS entity_projection_review (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    lens_id      uuid NOT NULL REFERENCES document_collections(id) ON DELETE CASCADE,
    char_start   int  NOT NULL,
    char_end     int  NOT NULL CHECK (char_end >= char_start),
    entity_type  text NOT NULL,
    decision     text NOT NULL CHECK (decision IN ('confirmed', 'rejected')),
    reviewer     uuid REFERENCES principals(id) ON DELETE SET NULL,
    rationale    text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Read back per (document, lens, span); the latest decision wins.
CREATE INDEX IF NOT EXISTS idx_epr_doc_lens_span
    ON entity_projection_review (document_id, lens_id, char_start, char_end, created_at DESC);

ALTER TABLE entity_projection_review ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_projection_review FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS epr_read ON entity_projection_review;
CREATE POLICY epr_read ON entity_projection_review
    FOR SELECT
    USING (docray_is_admin()
           OR document_id IN (SELECT * FROM docray_readable_documents()));

DROP POLICY IF EXISTS epr_write ON entity_projection_review;
CREATE POLICY epr_write ON entity_projection_review
    FOR ALL
    USING (docray_is_admin()
           OR document_id IN (SELECT * FROM docray_writable_documents()))
    WITH CHECK (docray_is_admin()
                OR document_id IN (SELECT * FROM docray_writable_documents()));

-- The API records the decision (docray_api inherits docray_client, migration 030);
-- the batch refresh reads it to re-apply. entity_projection itself stays batch-only.
GRANT SELECT ON entity_projection_review TO docray_api;
GRANT SELECT, INSERT ON entity_projection_review TO docray_client;
GRANT SELECT ON entity_projection_review TO docray_batch;

-- ==========================================================================
-- FR-5 / D-024 — lens_entity_stats: the read-model rollup for lensEntities, so
-- "which entities live in this lens, and how often" is O(result) on the read path,
-- never a live GROUP BY over entity_projection. Refreshed per lens by the batch job
-- (the aggregate runs at the pipeline boundary, which D-024 permits). Covers SCOPE
-- lenses only; the corpus-wide global entity distribution is served by the existing
-- canonical / entity_frequency surfaces (a global rollup would aggregate across
-- tenants). n_mentions/n_documents are lens-wide counts (a per-principal-exact count
-- is not precomputable for a non-summable distinct — same trade-off as corpus_stats,
-- migration 095); a read is gated on the caller seeing >= 1 of the lens's documents.
-- ==========================================================================

CREATE TABLE IF NOT EXISTS lens_entity_stats (
    lens_id      uuid   NOT NULL REFERENCES document_collections(id) ON DELETE CASCADE,
    canonical_id bigint NOT NULL REFERENCES canonical(id) ON DELETE CASCADE,
    scope        text   NOT NULL CHECK (scope IN ('local', 'global')),
    n_mentions   bigint NOT NULL DEFAULT 0,
    n_documents  bigint NOT NULL DEFAULT 0,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lens_id, canonical_id)
);
CREATE INDEX IF NOT EXISTS idx_lens_entity_stats_lens_mentions
    ON lens_entity_stats (lens_id, n_mentions DESC, canonical_id);

ALTER TABLE lens_entity_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE lens_entity_stats FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS lens_entity_stats_read ON lens_entity_stats;
CREATE POLICY lens_entity_stats_read ON lens_entity_stats
    FOR SELECT
    USING (
        docray_is_admin()
        OR lens_id IN (
            SELECT collection_id FROM document_collection_members
            WHERE document_id IN (SELECT * FROM docray_readable_documents())
        )
    );
DROP POLICY IF EXISTS lens_entity_stats_write ON lens_entity_stats;
CREATE POLICY lens_entity_stats_write ON lens_entity_stats
    FOR ALL USING (docray_is_admin()) WITH CHECK (docray_is_admin());

GRANT SELECT ON lens_entity_stats TO docray_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON lens_entity_stats TO docray_batch;

INSERT INTO schema_migrations (filename) VALUES ('129_entity_projection.sql')
    ON CONFLICT (filename) DO NOTHING;
