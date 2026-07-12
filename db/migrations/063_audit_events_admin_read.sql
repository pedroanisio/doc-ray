-- doc-ray: tenant-admin read surface for the security audit log.
--
-- BL-074 made audit_events insert-only for application principals. BL-075 adds
-- a tenant-scoped read policy for admin principals and keeps the mutation guard
-- intact: clients still cannot update or delete audit rows.

ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_audit_admin_select ON audit_events;
DROP POLICY IF EXISTS p_audit_insert ON audit_events;

CREATE POLICY p_audit_admin_select ON audit_events FOR SELECT
    USING (tenant_id = docray_current_tenant() AND docray_is_admin());

CREATE POLICY p_audit_insert ON audit_events FOR INSERT
    WITH CHECK (
        actor_principal_id IS NULL
        OR (
            actor_principal_id = docray_current_principal()
            AND tenant_id = docray_current_tenant()
        )
    );

GRANT SELECT ON audit_events TO docray_client;
REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON audit_events
    FROM docray_client;

INSERT INTO schema_migrations (filename) VALUES ('063_audit_events_admin_read.sql')
    ON CONFLICT (filename) DO NOTHING;
