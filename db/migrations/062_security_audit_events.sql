-- doc-ray: append-only security audit trail.
--
-- document_provenance is data/pipeline provenance. audit_events records
-- authenticated access, authorization denials, and authentication lifecycle
-- events with actor, target, source, and outcome fields.

CREATE TABLE IF NOT EXISTS audit_events (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid REFERENCES tenants(id) ON DELETE SET NULL,
    actor_principal_id  uuid REFERENCES principals(id) ON DELETE SET NULL,
    actor_subject       text,
    action              text NOT NULL,
    target_type         text NOT NULL,
    target_id           text,
    outcome             text NOT NULL
                            CHECK (outcome IN ('success', 'failure', 'denied')),
    source_ip           text,
    request_id          text,
    details             jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_events_actor
    ON audit_events (actor_principal_id, actor_subject, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_action
    ON audit_events (action, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_target
    ON audit_events (target_type, target_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_time
    ON audit_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_source_ip
    ON audit_events (source_ip, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_tenant
    ON audit_events (tenant_id, occurred_at DESC);

CREATE OR REPLACE FUNCTION docray_prevent_audit_event_mutation() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    is_super boolean;
BEGIN
    SELECT rolsuper INTO is_super FROM pg_roles WHERE rolname = current_user;
    IF NOT COALESCE(is_super, false) THEN
        RAISE EXCEPTION 'audit_events is append-only for non-superusers';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events;
CREATE TRIGGER trg_audit_events_append_only
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION docray_prevent_audit_event_mutation();

REVOKE ALL ON audit_events FROM PUBLIC;
GRANT INSERT ON audit_events TO docray_client;
REVOKE SELECT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON audit_events
    FROM docray_client;

INSERT INTO schema_migrations (filename) VALUES ('062_security_audit_events.sql')
    ON CONFLICT (filename) DO NOTHING;
