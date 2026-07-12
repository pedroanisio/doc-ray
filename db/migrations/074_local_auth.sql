-- doc-ray: local self-service auth credentials and verification tokens.
--
-- Admin remains seed/operator-managed. Self-registered users are principals
-- with the reader role only; document/data visibility continues to be decided
-- by the existing RLS helpers and ACLs.

CREATE TABLE IF NOT EXISTS principal_credentials (
    principal_id          uuid PRIMARY KEY REFERENCES principals(id) ON DELETE CASCADE,
    tenant_id             uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email_normalized      text NOT NULL CHECK (email_normalized = lower(email_normalized)),
    password_hash         text NOT NULL CHECK (password_hash LIKE '$argon2%'),
    password_algo         text NOT NULL DEFAULT 'argon2id' CHECK (password_algo = 'argon2id'),
    email_verified_at     timestamptz,
    disabled_at           timestamptz,
    failed_login_count    integer NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
    last_failed_login_at  timestamptz,
    last_login_at         timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email_normalized)
);

CREATE INDEX IF NOT EXISTS idx_principal_credentials_email
    ON principal_credentials (email_normalized);

CREATE TABLE IF NOT EXISTS email_verification_tokens (
    token_hash   text PRIMARY KEY CHECK (length(token_hash) = 64),
    principal_id uuid NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    expires_at   timestamptz NOT NULL,
    consumed_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_verification_tokens_principal
    ON email_verification_tokens (principal_id);

CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
    token_id     text PRIMARY KEY,
    family_id    text NOT NULL,
    principal_id uuid NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    subject      text NOT NULL,
    scopes       text[] NOT NULL,
    expires_at   timestamptz NOT NULL,
    status       text NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'rotated', 'revoked', 'expired')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_auth_refresh_tokens_family
    ON auth_refresh_tokens (family_id);
CREATE INDEX IF NOT EXISTS idx_auth_refresh_tokens_principal
    ON auth_refresh_tokens (principal_id);

INSERT INTO schema_migrations (filename)
VALUES ('074_local_auth.sql')
ON CONFLICT (filename) DO NOTHING;
