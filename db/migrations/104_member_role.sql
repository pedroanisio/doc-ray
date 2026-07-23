-- doc-ray: the `member` role — a registered user who can own a corpus.
--
-- Migration 102 made ownership the read boundary, which exposed the other half
-- of the problem: the default signup role `reader` carries only the `read`
-- permission, and every local login minted a read-only token, so a
-- self-registered user could never ingest anything. Per-owner isolation
-- therefore handed new accounts a permanently EMPTY corpus rather than a clean
-- one — correct isolation, unusable product.
--
-- `member` is that missing role: read + annotate + delete, which the API maps
-- to the coarse `write` token scope (application/local_auth.scopes_for_actions).
-- It is NOT a privilege escalation over `reader`: every mutation is still gated
-- per document by docray_writable_documents(), so a member may curate only what
-- they own or were granted. `redact` and `admin` remain admin-only.
--
-- `reader` is deliberately left read-only. SCIM provisioning and the admin CLI
-- both assign it for look-but-don't-touch accounts, so widening it in place
-- would have silently promoted every already-provisioned principal.

INSERT INTO roles (name) VALUES ('member')
    ON CONFLICT (name) DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id
      FROM roles r, permissions p
     WHERE r.name = 'member'
       AND p.action IN ('read', 'annotate', 'delete')
    ON CONFLICT DO NOTHING;

-- Promote the humans who self-registered under the old default. Scoped to
-- principals holding a LOCAL credential: SCIM/OIDC-provisioned readers and
-- service principals keep the read-only role they were deliberately given.
INSERT INTO principal_roles (principal_id, role_id)
    SELECT pc.principal_id, r.id
      FROM principal_credentials pc
      JOIN principals p ON p.id = pc.principal_id
      CROSS JOIN roles r
     WHERE r.name = 'member'
       AND p.kind = 'human'
    ON CONFLICT DO NOTHING;

INSERT INTO schema_migrations (filename) VALUES ('104_member_role.sql')
    ON CONFLICT (filename) DO NOTHING;
