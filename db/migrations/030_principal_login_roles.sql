-- doc-ray: principal LOGIN roles for hostile direct-SQL isolation (BL-012).
--
-- Migration 018 allowed the trusted API tier to assert app.principal_id after
-- SET ROLE docray_client. That is not safe for a user with direct SQL access as
-- docray_client: they can set the GUC to another principal. This migration keeps
-- the trusted app assertion path, but moves it to docray_api, and maps direct
-- database identities from the authenticated role name instead.

ALTER TABLE principals
    ADD COLUMN IF NOT EXISTS db_role name UNIQUE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'principals_db_role_not_reserved'
    ) THEN
        ALTER TABLE principals ADD CONSTRAINT principals_db_role_not_reserved
            CHECK (db_role IS NULL OR db_role NOT IN ('docray', 'docray_client', 'docray_api'));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docray_api') THEN
        CREATE ROLE docray_api NOLOGIN IN ROLE docray_client;
    ELSE
        GRANT docray_client TO docray_api;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION docray_effective_actor() RETURNS name
    LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN current_setting('role', true) IS NULL
          OR current_setting('role', true) = ''
          OR current_setting('role', true) = 'none'
            THEN session_user::name
        ELSE current_setting('role', true)::name
    END
$$;

CREATE OR REPLACE FUNCTION docray_current_principal() RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT COALESCE(
        (
            SELECT p.id
              FROM principals p
             WHERE p.db_role = docray_effective_actor()
             LIMIT 1
        ),
        CASE
            WHEN docray_effective_actor() = 'docray_api'::name
                THEN nullif(current_setting('app.principal_id', true), '')::uuid
            ELSE NULL
        END
    )
$$;

CREATE OR REPLACE FUNCTION docray_create_principal_login(
    principal uuid,
    role_name name,
    password text DEFAULT NULL
) RETURNS name
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF role_name IN ('docray', 'docray_client', 'docray_api') THEN
        RAISE EXCEPTION 'reserved doc-ray role cannot be bound to a principal: %', role_name;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM principals WHERE id = principal) THEN
        RAISE EXCEPTION 'unknown principal: %', principal;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name::text) THEN
        IF password IS NULL THEN
            EXECUTE format('CREATE ROLE %I LOGIN IN ROLE docray_client', role_name);
        ELSE
            EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L IN ROLE docray_client',
                           role_name, password);
        END IF;
    ELSE
        EXECUTE format('GRANT docray_client TO %I', role_name);
    END IF;
    UPDATE principals SET db_role = role_name WHERE id = principal;
    RETURN role_name;
END;
$$;

REVOKE ALL ON FUNCTION docray_effective_actor(), docray_current_principal(),
                       docray_create_principal_login(uuid, name, text)
    FROM PUBLIC;

INSERT INTO schema_migrations (filename) VALUES ('030_principal_login_roles.sql')
    ON CONFLICT (filename) DO NOTHING;
