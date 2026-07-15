-- doc-ray: OAuth 2.1 Authorization Server storage for the MCP connector flow.
--
-- Backs the webapp's OAuth endpoints (Dynamic Client Registration + the
-- authorization-code/PKCE flow) that let an MCP client such as Claude Desktop
-- obtain an RLS-bound doc-ray access JWT without a pre-shared token. Written
-- only through the privileged application connection (mirroring 074_local_auth):
-- these tables hold auth plumbing, not corpus content, so they sit outside RLS.

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id                  text PRIMARY KEY,
    client_secret_hash         text,                       -- NULL for public (PKCE) clients
    redirect_uris              text[] NOT NULL CHECK (cardinality(redirect_uris) > 0),
    client_name                text,
    token_endpoint_auth_method text NOT NULL DEFAULT 'none'
        CHECK (token_endpoint_auth_method IN ('none', 'client_secret_post', 'client_secret_basic')),
    grant_types                text[] NOT NULL DEFAULT ARRAY['authorization_code', 'refresh_token'],
    scope                      text NOT NULL DEFAULT 'read',
    created_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
    code_hash             text PRIMARY KEY CHECK (length(code_hash) = 64),
    client_id             text NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    redirect_uri          text NOT NULL,
    code_challenge        text NOT NULL,
    code_challenge_method  text NOT NULL DEFAULT 'S256' CHECK (code_challenge_method = 'S256'),
    principal_id          uuid NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    subject               text NOT NULL,
    scope                 text NOT NULL DEFAULT 'read',
    resource              text,
    expires_at            timestamptz NOT NULL,
    consumed_at           timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_oauth_authorization_codes_client
    ON oauth_authorization_codes (client_id);
CREATE INDEX IF NOT EXISTS idx_oauth_authorization_codes_expires
    ON oauth_authorization_codes (expires_at);

INSERT INTO schema_migrations (filename)
VALUES ('082_oauth_clients.sql')
ON CONFLICT (filename) DO NOTHING;
