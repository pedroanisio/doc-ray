-- doc-ray: bind an OAuth client to a service principal for the client_credentials grant.
--
-- Open Dynamic Client Registration is anonymous, so a self-registered client MUST
-- NOT be able to obtain a token via client_credentials — that would let anyone mint
-- an RLS-bound token for an arbitrary principal. This column is the authorization
-- gate: only a confidential client an operator has explicitly bound to a service
-- principal here may use client_credentials. NULL (the default, and all DCR clients)
-- means the grant is refused. Additive column, no RLS change (see 082_oauth_clients).

ALTER TABLE oauth_clients
    ADD COLUMN IF NOT EXISTS principal_id uuid REFERENCES principals(id) ON DELETE SET NULL;

INSERT INTO schema_migrations (filename)
VALUES ('087_oauth_client_service_principal.sql')
ON CONFLICT (filename) DO NOTHING;
