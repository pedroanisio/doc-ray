-- doc-ray: document-level RBAC enforced with PostgreSQL Row-Level Security.
--
-- MODEL
--   principal  = an app identity (human or service)
--   role       = named bundle of permissions (reader / curator / admin)
--   permission = an action verb (read / annotate / delete / redact / admin)
--   document_acl(document_id, principal_id, permission) = the per-document grant
--   A principal may READ a document iff admin OR it holds any ACL row on it, and
--   may WRITE iff admin OR holds an annotate/delete/admin ACL. Every
--   document-scoped table inherits the decision through its owning document.
--
-- ENFORCEMENT / TRUST BOUNDARY  (read before assuming it "just works")
--   * `docray` is a SUPERUSER and OWNS these tables, so it BYPASSES RLS. The
--     ingest/annotation pipeline + admin tooling keep using that connection —
--     it is the trusted batch writer. RLS does NOT constrain it (by design).
--   * End-user / API traffic MUST run as the non-superuser role `docray_client`
--     (via `SET ROLE docray_client`) and set the principal per transaction:
--         SET app.principal_id = '<principal uuid>';
--     Only then do the policies bite. A superuser/owner connection sees all.
--   * The principal comes from a session GUC the connecting role can set, so
--     this is safe ONLY when the DB client is the trusted app tier. A client
--     able to run arbitrary SQL as docray_client can impersonate any principal.
--     For hostile direct access, give each principal a LOGIN role and key
--     policies on current_user instead.

-- =============================== RBAC tables ================================

CREATE TABLE IF NOT EXISTS principals (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind             text NOT NULL DEFAULT 'human'
                       CHECK (kind IN ('human', 'service')),
    display_name     text NOT NULL UNIQUE,
    external_subject text,                            -- OIDC sub / LDAP dn (authN federation)
    disabled_at      timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS permissions (
    id     bigserial PRIMARY KEY,
    action text NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS roles (
    id   bigserial PRIMARY KEY,
    name text NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id       bigint NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id bigint NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS principal_roles (
    principal_id uuid   NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    role_id      bigint NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (principal_id, role_id)
);

CREATE TABLE IF NOT EXISTS document_acl (
    document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    principal_id uuid NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    permission   text NOT NULL REFERENCES permissions(action),
    granted_at   timestamptz NOT NULL DEFAULT now(),
    granted_by   uuid REFERENCES principals(id),
    PRIMARY KEY (document_id, principal_id, permission)
);
CREATE INDEX IF NOT EXISTS idx_document_acl_principal ON document_acl (principal_id);
CREATE INDEX IF NOT EXISTS idx_document_acl_document  ON document_acl (document_id);

-- Formalize the free-text provenance actor into an auditable principal link.
-- The text `actor` stays as the immutable historical label (system actors like
-- 'object-store'/'pypdf' and run ids are not principals); SET NULL on delete so
-- erasing a principal never destroys the audit row.
ALTER TABLE document_provenance
    ADD COLUMN IF NOT EXISTS actor_principal_id uuid REFERENCES principals(id) ON DELETE SET NULL;

-- =============================== seed baseline =============================

INSERT INTO permissions (action) VALUES
    ('read'), ('annotate'), ('delete'), ('redact'), ('admin')
    ON CONFLICT (action) DO NOTHING;

INSERT INTO roles (name) VALUES ('reader'), ('curator'), ('admin')
    ON CONFLICT (name) DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id FROM roles r, permissions p
    WHERE (r.name = 'reader'  AND p.action IN ('read'))
       OR (r.name = 'curator' AND p.action IN ('read', 'annotate'))
       OR (r.name = 'admin'   AND p.action IN ('read', 'annotate', 'delete', 'redact', 'admin'))
    ON CONFLICT DO NOTHING;

INSERT INTO principals (kind, display_name) VALUES
    ('human', 'bootstrap-admin'), ('service', 'pipeline')
    ON CONFLICT (display_name) DO NOTHING;
INSERT INTO principal_roles (principal_id, role_id)
    SELECT pr.id, r.id FROM principals pr, roles r
    WHERE pr.display_name = 'bootstrap-admin' AND r.name = 'admin'
    ON CONFLICT DO NOTHING;

-- =============================== helpers ===================================
-- The two set-returning helpers compute the caller's visible-document set ONCE
-- per query (the planner treats them as an InitPlan); policies then semi-join
-- against that set instead of calling a per-row resolver a million times. All
-- SECURITY DEFINER (owned by the superuser) so they read the RBAC tables and the
-- owning document WITHOUT being filtered by RLS (no recursion, no need to grant
-- clients access to the identity tables). search_path pinned against hijacking.
-- They take NO row/id argument, so they cannot be used to probe arbitrary rows.

CREATE OR REPLACE FUNCTION docray_current_principal() RETURNS uuid
    LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('app.principal_id', true), '')::uuid $$;

CREATE OR REPLACE FUNCTION docray_is_admin() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS
$$ SELECT EXISTS (
       SELECT 1 FROM principal_roles pr
       JOIN role_permissions rp ON rp.role_id = pr.role_id
       JOIN permissions p       ON p.id = rp.permission_id
       WHERE pr.principal_id = docray_current_principal()
         AND p.action = 'admin') $$;

-- Documents the caller may READ: all (if admin) or those with any ACL grant.
CREATE OR REPLACE FUNCTION docray_readable_documents() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS
$$ SELECT id FROM documents WHERE docray_is_admin()
   UNION
   SELECT document_id FROM document_acl WHERE principal_id = docray_current_principal() $$;

-- Documents the caller may WRITE: all (if admin) or those with a write ACL.
CREATE OR REPLACE FUNCTION docray_writable_documents() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS
$$ SELECT id FROM documents WHERE docray_is_admin()
   UNION
   SELECT document_id FROM document_acl
   WHERE principal_id = docray_current_principal()
     AND permission IN ('annotate', 'delete', 'admin') $$;

-- Only the two set helpers are ever named directly in a policy, so only they
-- need EXECUTE by the client. Lock the rest (and PUBLIC) down.
REVOKE ALL ON FUNCTION docray_current_principal(), docray_is_admin(),
                       docray_readable_documents(), docray_writable_documents()
    FROM PUBLIC;

-- =============================== attach RLS ================================
-- Each table gets a read policy (semi-join vs. readable_documents) and separate
-- write policies (vs. writable_documents, incl. WITH CHECK so a row cannot be
-- inserted or moved into a document you cannot write). `tmpl` maps the row to
-- its owning document via set membership; %DOCS% is the read/write set.
--
-- Writes are FOR INSERT/UPDATE/DELETE, NOT FOR ALL: a FOR ALL policy's USING is
-- OR-combined with the read policy on SELECT, which forces the planner to
-- evaluate BOTH subqueries per read and defeats semi-join pullup (catastrophic
-- on million-row tables). Keeping writes off SELECT lets reads use the single
-- read predicate as a clean semi-join.

CREATE OR REPLACE FUNCTION docray_attach_rls(tbl regclass, tmpl text) RETURNS void
    LANGUAGE plpgsql AS $fn$
DECLARE
    read_pred  text := replace(tmpl, '%DOCS%', 'SELECT * FROM docray_readable_documents()');
    write_pred text := replace(tmpl, '%DOCS%', 'SELECT * FROM docray_writable_documents()');
    p text;
BEGIN
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
    FOREACH p IN ARRAY ARRAY['p_read', 'p_write', 'p_ins', 'p_upd', 'p_del'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %s', p, tbl);   -- p_write: legacy name
    END LOOP;
    EXECUTE format('CREATE POLICY p_read ON %s FOR SELECT USING (%s)', tbl, read_pred);
    EXECUTE format('CREATE POLICY p_ins ON %s FOR INSERT WITH CHECK (%s)', tbl, write_pred);
    EXECUTE format('CREATE POLICY p_upd ON %s FOR UPDATE USING (%s) WITH CHECK (%s)',
                   tbl, write_pred, write_pred);
    EXECUTE format('CREATE POLICY p_del ON %s FOR DELETE USING (%s)', tbl, write_pred);
END;
$fn$;

DO $$
DECLARE
    m record;
    ann  constant text := 'annotation_id IN (SELECT id FROM annotations WHERE document_id IN (%DOCS%))';
    tok  constant text := 'token_id IN (SELECT id FROM tokens WHERE annotation_id IN '
                          '(SELECT id FROM annotations WHERE document_id IN (%DOCS%)))';
    spn  constant text := 'span_id IN (SELECT id FROM spans WHERE document_id IN (%DOCS%))';
BEGIN
    FOR m IN
        SELECT * FROM (VALUES
            ('documents',            'id IN (%DOCS%)'),
            ('document_content',     'document_id IN (%DOCS%)'),
            ('document_authors',     'document_id IN (%DOCS%)'),
            ('document_tags',        'document_id IN (%DOCS%)'),
            ('document_identifiers', 'document_id IN (%DOCS%)'),
            ('document_metadata',    'document_id IN (%DOCS%)'),
            ('document_provenance',  'document_id IN (%DOCS%)'),
            ('provenance_params',    'event_id IN (SELECT id FROM document_provenance WHERE document_id IN (%DOCS%))'),
            ('sentences',            'document_id IN (%DOCS%)'),
            ('spans',                'document_id IN (%DOCS%)'),
            ('annotations',          'document_id IN (%DOCS%)'),
            ('coref_chain',          'document_id IN (%DOCS%)'),
            ('coref_mention',        spn),
            ('lexeme_occurrences',   ann),
            ('tokens',               ann),
            ('token_feature',        tok),
            ('subword',              tok),
            ('chunk',                ann),
            ('constituent',          ann),
            ('dependency',           ann),
            ('entity_mention',       ann),
            ('entity_link',          'mention_id IN (SELECT id FROM entity_mention WHERE annotation_id IN '
                                     '(SELECT id FROM annotations WHERE document_id IN (%DOCS%)))'),
            ('srl_predicate',        ann),
            ('srl_argument',         spn),
            ('word_sense',           ann),
            ('amr_node',             ann),
            ('amr_edge',             'parent_node_id IN (SELECT id FROM amr_node WHERE annotation_id IN '
                                     '(SELECT id FROM annotations WHERE document_id IN (%DOCS%)))'),
            ('discourse_relation',   ann),
            ('timex',                ann),
            ('event',                ann),
            ('tlink',                ann),
            ('sentiment',            ann),
            ('embedding',            spn)
        ) AS t(tbl, tmpl)
    LOOP
        PERFORM docray_attach_rls(m.tbl::regclass, m.tmpl);
    END LOOP;
END $$;

-- The derived views select document_content.full_text across ALL documents and
-- are owned by the superuser, so WITHOUT security_invoker they would run with
-- owner privileges and BYPASS the RLS above — a cross-document text leak. Force
-- them to run as the invoking principal so base-table RLS applies. (PG15+.)
ALTER VIEW lexeme_occurrence_detail SET (security_invoker = true);
ALTER VIEW lexeme_forms             SET (security_invoker = true);
ALTER VIEW lexeme_keys              SET (security_invoker = true);

-- =============================== client role ===============================
-- Non-superuser, non-owner => RLS actually applies. NOLOGIN: the app connects as
-- its login user and `SET ROLE docray_client`. Grants are an explicit ALLOWLIST
-- (never GRANT ON ALL TABLES — that would auto-expose future tables/views and
-- was how the view leak happened): only RLS-filtered document tables, the global
-- vocab, the now-invoker-safe views, and EXECUTE on the two policy set-helpers.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docray_client') THEN
        CREATE ROLE docray_client NOLOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO docray_client;

-- Self-heal: clear any prior grants (e.g. a superseded GRANT ON ALL TABLES) so
-- the final privilege set is EXACTLY this allowlist regardless of prior state.
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM docray_client;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM docray_client;

DO $$
DECLARE
    t text;
    readable text[] := ARRAY[
        'documents','document_content','document_authors','document_tags',
        'document_identifiers','document_metadata','document_provenance','provenance_params',
        'sentences','spans','annotations','coref_chain','coref_mention','lexeme_occurrences',
        'tokens','token_feature','subword','chunk','constituent','dependency','entity_mention',
        'entity_link','srl_predicate','srl_argument','word_sense','amr_node','amr_edge',
        'discourse_relation','timex','event','tlink','sentiment','embedding',
        -- global vocab (no document payload) + the invoker-safe views
        'lexemes','authors','tags',
        'lexeme_occurrence_detail','lexeme_forms','lexeme_keys'
    ];
BEGIN
    FOREACH t IN ARRAY readable LOOP
        EXECUTE format('GRANT SELECT ON %I TO docray_client', t);
    END LOOP;
END $$;

GRANT EXECUTE ON FUNCTION docray_readable_documents(), docray_writable_documents()
    TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('018_rbac.sql')
    ON CONFLICT (filename) DO NOTHING;
