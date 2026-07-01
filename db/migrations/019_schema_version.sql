-- doc-ray: record the schema's semantic version (SemVer major.minor.patch).
--
-- CLAUDE.md: "all schema files use semantic versioning." This is the single,
-- queryable version of the *applied* schema contract — distinct from the
-- schema_migrations ledger (which is a per-file audit log). Migrations that
-- change the contract bump this: PATCH for fixes, MINOR for backward-compatible
-- additions, MAJOR for breaking changes.
--
-- 1.0.0 = first tagged baseline: the 4NF document + provenance model, the shared
-- span/annotation backbone, the full 20-layer NLP stack (001–015), language-aware
-- FTS + schema-review fixes (016–017), and document-level RLS/RBAC (018).

CREATE TABLE IF NOT EXISTS schema_version (
    singleton  boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version    text NOT NULL CHECK (version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_version (singleton, version) VALUES (true, '1.0.0')
    ON CONFLICT (singleton) DO NOTHING;

-- Convenience accessor (STABLE; reads the single row).
CREATE OR REPLACE FUNCTION docray_schema_version() RETURNS text
    LANGUAGE sql STABLE AS $$ SELECT version FROM schema_version WHERE singleton $$;

INSERT INTO schema_migrations (filename) VALUES ('019_schema_version.sql')
    ON CONFLICT (filename) DO NOTHING;
