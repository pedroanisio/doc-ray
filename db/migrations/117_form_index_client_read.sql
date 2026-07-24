-- doc-ray: grant the API/webapp role SELECT on the remissive index (form_index).
--
-- BUG: every entityProfiles / entity_profiles call failed for real callers with
--   `psycopg.errors.InsufficientPrivilege: permission denied for table form_index`
-- (adapters/persistence_read_structure.py::entity_profiles joins form_index ->
-- canonical to report whether a reconciled entity is backed by the canonical
-- registry). The API and webapp run as docray_api, which inherits docray_client
-- (migration 030: docray_api IN ROLE docray_client). Migration 097 added
-- `canonical` to docray_client's SELECT allowlist when the entity overlay needed
-- it; form_index — its sibling, added by the same layer (migration 022) — was
-- never added, and nothing read it from a client role until BL-146.
--
-- form_index is a shared registry (surface form -> canonical id; no tenant_id, no
-- document payload), the same class as canonical/lexemes/authors/tags that 018
-- and 097 already grant. Confidentiality is unchanged: the read path only ever
-- looks up form_norm keys derived from entity rows the caller could already see
-- through the RLS-scoped frequency read, so this grants label resolution for
-- entities the caller's own documents already reference — exactly the argument
-- migration 097 made for canonical.
--
-- Grant-only, no schema change: docray_schema_version() is untouched (as in 097).

GRANT SELECT ON form_index TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('117_form_index_client_read.sql')
    ON CONFLICT (filename) DO NOTHING;
