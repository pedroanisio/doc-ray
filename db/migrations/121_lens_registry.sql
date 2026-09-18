-- Lens Registry: promote collections to named scopes, migration 121.
--
-- A *lens* is a named scope under which a later projection layer (issue #8)
-- will interpret entity identity. This block ships VOCABULARY AND METADATA ONLY:
-- it promotes the existing document_collections container (migration 047) into a
-- lens by flagging it `is_scope`, gives a document a `default_lens_id`, and
-- reserves a `global` lens. It changes NOTHING about how any entity resolves
-- today — every existing read returns byte-identical results. See
-- docs/design/entity-fabric.md (§3.2, §7) and
-- doc_ray.domain.lenses for the pure cycle/naming rules this trigger mirrors.
--
-- FR-2 integrity note (recorded decision). The issue prefers a composite FK
-- against UNIQUE (id, is_scope) to keep the is_scope rule declarative. That form
-- needs a generated companion column in the FK, and PostgreSQL 16 rejects
-- `ON DELETE SET NULL` on any FK that contains a generated column
-- ("invalid ON DELETE action for foreign key constraint containing generated
-- column"). FR-1 mandates ON DELETE SET NULL on both new FKs, so the composite
-- form is unusable here. FR-2 explicitly permits a trigger instead; we take it.
-- The plain FKs below give referential integrity and the required SET NULL; the
-- is_scope, membership, cycle and reserved-name rules are enforced by the
-- BEFORE triggers, which — like an FK — only a superuser can bypass.

-- --------------------------------------------------------------------------
-- Columns (additive; ADD COLUMN IF NOT EXISTS makes the whole migration a
-- no-op on re-run, inline REFERENCES included).
-- --------------------------------------------------------------------------

ALTER TABLE document_collections
    ADD COLUMN IF NOT EXISTS is_scope boolean NOT NULL DEFAULT false;

ALTER TABLE document_collections
    ADD COLUMN IF NOT EXISTS parent_lens_id uuid
        REFERENCES document_collections(id) ON DELETE SET NULL;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS default_lens_id uuid
        REFERENCES document_collections(id) ON DELETE SET NULL;

-- --------------------------------------------------------------------------
-- Indexes. The lenses read path filters `WHERE is_scope` and pages by
-- (name, id) keyset, so the partial index carries both the predicate and the
-- ORDER BY (D-024: index the read path). FR-1 asked for `(id) WHERE is_scope`;
-- `(name, id)` is a superset that also serves the keyset scan — recorded as a
-- deliberate refinement. Both new foreign keys are indexed (perf NFR).
-- --------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_document_collections_scope
    ON document_collections (name, id) WHERE is_scope;
CREATE INDEX IF NOT EXISTS idx_document_collections_parent_lens
    ON document_collections (parent_lens_id) WHERE parent_lens_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_documents_default_lens
    ON documents (default_lens_id) WHERE default_lens_id IS NOT NULL;

-- --------------------------------------------------------------------------
-- Integrity trigger on document_collections: parent must be a scope, a child
-- with a parent must itself be a scope, no parent cycle, a lens that parents
-- another lens cannot be demoted, and `global` cannot be renamed or demoted.
-- Mirrors doc_ray.domain.lenses.would_create_cycle / is_reserved_name.
-- --------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION docray_lens_collection_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent_is_scope boolean;
    walker uuid;
    steps int := 0;
BEGIN
    -- The reserved `global` lens is unrenamable and cannot stop being a scope.
    IF TG_OP = 'UPDATE' AND OLD.name = 'global' THEN
        IF NEW.name IS DISTINCT FROM 'global' THEN
            RAISE EXCEPTION 'the global lens cannot be renamed'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.is_scope IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'the global lens must remain a scope'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- Demoting a lens that still parents another lens would strand the child's
    -- parent_lens_id on a non-scope row; refuse it (demote the children first).
    IF TG_OP = 'UPDATE' AND OLD.is_scope AND NOT NEW.is_scope
       AND EXISTS (SELECT 1 FROM document_collections
                   WHERE parent_lens_id = NEW.id) THEN
        RAISE EXCEPTION 'collection % still parents a lens and cannot be demoted', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.parent_lens_id IS NULL THEN
        RETURN NEW;
    END IF;

    -- Only a scope may have a parent, and the parent must itself be a scope.
    IF NOT NEW.is_scope THEN
        RAISE EXCEPTION 'only a scope collection (a lens) may have a parent lens'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT is_scope INTO parent_is_scope
        FROM document_collections WHERE id = NEW.parent_lens_id;
    IF parent_is_scope IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'parent_lens_id % must reference a scope collection', NEW.parent_lens_id
            USING ERRCODE = 'check_violation';
    END IF;

    -- No cycle: walk parents from the proposed parent; reaching NEW.id loops.
    walker := NEW.parent_lens_id;
    WHILE walker IS NOT NULL LOOP
        IF walker = NEW.id THEN
            RAISE EXCEPTION 'parent_lens_id % would create a lens cycle', NEW.parent_lens_id
                USING ERRCODE = 'check_violation';
        END IF;
        steps := steps + 1;
        IF steps > 10000 THEN  -- defensive: a pre-existing loop must not hang the write
            RAISE EXCEPTION 'lens parent chain too deep (possible cycle)'
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT parent_lens_id INTO walker FROM document_collections WHERE id = walker;
    END LOOP;

    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_lens_collection_guard ON document_collections;
CREATE TRIGGER trg_lens_collection_guard
    BEFORE INSERT OR UPDATE OF name, is_scope, parent_lens_id ON document_collections
    FOR EACH ROW EXECUTE FUNCTION docray_lens_collection_guard();

-- The reserved `global` lens is undeletable (enforced in the database, not app).
CREATE OR REPLACE FUNCTION docray_lens_global_delete_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.name = 'global' THEN
        RAISE EXCEPTION 'the global lens cannot be deleted'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
END $$;

DROP TRIGGER IF EXISTS trg_lens_global_delete_guard ON document_collections;
CREATE TRIGGER trg_lens_global_delete_guard
    BEFORE DELETE ON document_collections
    FOR EACH ROW EXECUTE FUNCTION docray_lens_global_delete_guard();

-- --------------------------------------------------------------------------
-- Integrity trigger on documents: a default_lens_id must reference a scope
-- collection the document is a member of. Membership lives in
-- document_collection_members (an explicit row), so a declarative constraint
-- cannot span it — enforce here.
-- --------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION docray_document_default_lens_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    lens_is_scope boolean;
BEGIN
    IF NEW.default_lens_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT is_scope INTO lens_is_scope
        FROM document_collections WHERE id = NEW.default_lens_id;
    IF lens_is_scope IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'default_lens_id % must reference a scope collection (a lens)',
            NEW.default_lens_id USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM document_collection_members
                   WHERE collection_id = NEW.default_lens_id AND document_id = NEW.id) THEN
        RAISE EXCEPTION 'document % is not a member of lens %', NEW.id, NEW.default_lens_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_document_default_lens_guard ON documents;
CREATE TRIGGER trg_document_default_lens_guard
    BEFORE INSERT OR UPDATE OF default_lens_id ON documents
    FOR EACH ROW EXECUTE FUNCTION docray_document_default_lens_guard();

-- Removing a document from a collection that is its default lens must not leave
-- a dangling default; clear it (the non-destructive, SET NULL-like choice). Runs
-- as the invoker: the caller already holds a write grant on the document (the
-- membership edit is guarded by it) and the column grant below permits the write.
CREATE OR REPLACE FUNCTION docray_membership_clears_default_lens() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE documents SET default_lens_id = NULL
        WHERE id = OLD.document_id AND default_lens_id = OLD.collection_id;
    RETURN OLD;
END $$;

DROP TRIGGER IF EXISTS trg_membership_clears_default_lens ON document_collection_members;
CREATE TRIGGER trg_membership_clears_default_lens
    BEFORE DELETE ON document_collection_members
    FOR EACH ROW EXECUTE FUNCTION docray_membership_clears_default_lens();

-- --------------------------------------------------------------------------
-- demoteLens must refuse while ANY document names the lens as its default —
-- including documents the demoting caller cannot see. A count read under the
-- caller's RLS would miss those, so this helper runs BYPASSRLS. It reads the
-- forced-RLS `documents` table (migration 106), so per migration 114 it must be
-- owned by a BYPASSRLS role (docray_batch); 114's auto-reassignment ran before
-- this migration existed, so we reassign it here explicitly, the same guarded way.
-- --------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION docray_lens_default_document_count(lens_id uuid)
RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT count(*) FROM documents WHERE default_lens_id = lens_id
$$;

DO $$
BEGIN
    ALTER FUNCTION docray_lens_default_document_count(uuid) OWNER TO docray_batch;
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE EXCEPTION
            'cannot reassign docray_lens_default_document_count to docray_batch: it lacks '
            'CREATE on schema public. Run as the cluster superuser, then re-run migrations:  '
            'GRANT CREATE ON SCHEMA public TO docray_batch;';
END
$$;

REVOKE ALL ON FUNCTION docray_lens_default_document_count(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION docray_lens_default_document_count(uuid) TO docray_client;

-- --------------------------------------------------------------------------
-- Grants. document_collections is already fully writable by docray_client
-- (migration 047), so promote/demote (UPDATE is_scope, parent_lens_id) need no
-- new grant. documents is SELECT-only for docray_client (docray_batch owns its
-- writes) — grant exactly the one column setDocumentDefaultLens writes, nothing
-- more (least privilege; docray_api inherits it via migration 030).
-- --------------------------------------------------------------------------

GRANT UPDATE (default_lens_id) ON documents TO docray_client;

-- --------------------------------------------------------------------------
-- Seed the reserved `global` lens idempotently. It is the default scope for
-- real-world (Wikidata-linkable) identities; scope lenses layer beside it.
-- --------------------------------------------------------------------------

INSERT INTO document_collections (name, description, kind, is_scope)
VALUES (
    'global',
    'The default lens: real-world (Wikidata-linkable) entity identity, shared across the whole corpus.',
    'manual',
    true
)
ON CONFLICT (name) DO NOTHING;

INSERT INTO schema_migrations (filename) VALUES ('121_lens_registry.sql')
    ON CONFLICT (filename) DO NOTHING;
