-- doc-ray: seed suggestions — the curatable staging area for domain seeding.
--
-- Automating domain seeding end-to-end is unsafe: live-fire runs repeatedly
-- produced plausible-but-wrong KB resolutions (a fuzzy 'versal' -> Versailles,
-- a bare 'Albertus' -> Albertus Magnus, a disambiguation-page QID). The fix is
-- a staging table: a worker job mines and resolves candidates into rows a human
-- curates (approve/reject) before anything touches the shared canonical
-- registry. The audit step becomes schema, not heroics.

CREATE TABLE IF NOT EXISTS seed_suggestion (
    id           ulid PRIMARY KEY,
    document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    term         text NOT NULL,                -- surface mined from the document
    occurrences  int  NOT NULL DEFAULT 0,      -- how often it appears
    source       text NOT NULL DEFAULT 'mention',  -- mention | lexeme
    qid          text,                         -- resolved Wikidata id; NULL = no match
    label        text,                         -- KB label (audit display)
    description  text,                         -- KB description (the mis-seed catcher)
    entity_type  text,                         -- mapped P31 type
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'approved', 'rejected', 'seeded')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    decided_at   timestamptz,
    UNIQUE (document_id, term)
);
CREATE INDEX IF NOT EXISTS idx_seed_suggestion_doc ON seed_suggestion (document_id, status);

INSERT INTO schema_migrations (filename) VALUES ('025_seed_suggestion.sql')
    ON CONFLICT (filename) DO NOTHING;
