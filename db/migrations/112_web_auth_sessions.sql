-- doc-ray: durable webapp auth sessions.
--
-- The Flask session cookie carries only an opaque session id; the principal
-- identity, expiry, and (optionally) the forwardable upstream bearer live
-- server-side so logout and TTL expiry invalidate access even if a browser
-- keeps an old signed cookie. That server-side store used to be a process-local
-- dict in webapp/auth_session.py, so a webapp restart — or a second replica —
-- silently logged every user out. This table makes it durable and shared.
--
-- principal_id is text (not a principals FK): a session may be established by a
-- federated OIDC/SAML identity that has no local principals row, and the store
-- must not reject those logins. Rows are cleaned up lazily on read past expiry;
-- the expires_at index also supports a periodic sweep.
--
-- Additive, backward-compatible: it does not write schema_version.

CREATE TABLE IF NOT EXISTS web_auth_sessions (
    session_id   text PRIMARY KEY,
    principal_id text NOT NULL,
    subject      text NOT NULL,
    bearer_token text,
    issued_at    timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_web_auth_sessions_principal
    ON web_auth_sessions (principal_id);
CREATE INDEX IF NOT EXISTS idx_web_auth_sessions_expires
    ON web_auth_sessions (expires_at);

INSERT INTO schema_migrations (filename) VALUES ('112_web_auth_sessions.sql')
    ON CONFLICT (filename) DO NOTHING;
