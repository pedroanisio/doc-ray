-- doc-ray: self-service account settings — UI preferences and email-change flow.
--
-- Both tables are written only through the privileged application connection
-- (mirroring 074_local_auth's principal_credentials): they hold no corpus
-- content and carry no authorization weight, so they are intentionally NOT
-- placed under RLS. `principal_preferences` stores cosmetic reading defaults
-- that follow a user across devices; `email_change_requests` backs the two-step,
-- ownership-proving email change (a token mailed to the NEW address confirms
-- the move, so a typo can never strand an account).

CREATE TABLE IF NOT EXISTS principal_preferences (
    principal_id        uuid PRIMARY KEY REFERENCES principals(id) ON DELETE CASCADE,
    theme               text NOT NULL DEFAULT 'system'
                        CHECK (theme IN ('system', 'light', 'dark')),
    default_reader_view text NOT NULL DEFAULT 'annotated'
                        CHECK (default_reader_view IN ('annotated', 'plain')),
    rows_per_page       integer NOT NULL DEFAULT 50
                        CHECK (rows_per_page BETWEEN 10 AND 200),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS email_change_requests (
    token_hash           text PRIMARY KEY CHECK (length(token_hash) = 64),
    principal_id         uuid NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    new_email_normalized text NOT NULL
                         CHECK (new_email_normalized = lower(new_email_normalized)),
    expires_at           timestamptz NOT NULL,
    consumed_at          timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_email_change_requests_principal
    ON email_change_requests (principal_id);

INSERT INTO schema_migrations (filename)
VALUES ('079_account_settings.sql')
ON CONFLICT (filename) DO NOTHING;
