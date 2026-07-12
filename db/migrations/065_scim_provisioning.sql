-- doc-ray: SCIM-managed principal provisioning (BL-068).
--
-- SCIM is authoritative for rows it manages. The app marks sync writes with
-- app.scim_sync=on; local role edits against SCIM-managed principals are
-- rejected so drift is visible instead of silently overriding the IdP.

ALTER TABLE principals
    ADD COLUMN IF NOT EXISTS managed_by text,
    ADD COLUMN IF NOT EXISTS managed_source_id text,
    ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'principals_managed_by_known'
    ) THEN
        ALTER TABLE principals ADD CONSTRAINT principals_managed_by_known
            CHECK (managed_by IS NULL OR managed_by IN ('scim'));
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_principals_managed_source
    ON principals (tenant_id, managed_by, managed_source_id)
    WHERE managed_by IS NOT NULL AND managed_source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS scim_groups (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_id text NOT NULL,
    display_name text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source_id)
);

CREATE TABLE IF NOT EXISTS scim_group_members (
    group_id uuid NOT NULL REFERENCES scim_groups(id) ON DELETE CASCADE,
    principal_id uuid NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, principal_id)
);

CREATE OR REPLACE FUNCTION docray_reject_manual_scim_role_edit()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    target_principal uuid;
BEGIN
    target_principal := COALESCE(NEW.principal_id, OLD.principal_id);
    IF current_setting('app.scim_sync', true) = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    IF EXISTS (
        SELECT 1 FROM principals
         WHERE id = target_principal
           AND managed_by = 'scim'
    ) THEN
        RAISE EXCEPTION 'SCIM-managed principal roles must be changed through SCIM sync';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_reject_manual_scim_role_edit ON principal_roles;
CREATE TRIGGER trg_reject_manual_scim_role_edit
    BEFORE INSERT OR UPDATE OR DELETE ON principal_roles
    FOR EACH ROW EXECUTE FUNCTION docray_reject_manual_scim_role_edit();

INSERT INTO schema_migrations (filename) VALUES ('065_scim_provisioning.sql')
    ON CONFLICT (filename) DO NOTHING;
