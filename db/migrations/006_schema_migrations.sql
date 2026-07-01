-- doc-ray: record which migrations have been applied.
--
-- Migrations otherwise run only via /docker-entrypoint-initdb.d on FIRST init of
-- an empty data dir, with no record of what ran. This table makes the applied
-- set queryable and lets a runner skip already-applied files on an existing
-- volume. (The table that tracks migrations is itself a migration — the usual
-- bootstrap pattern; created IF NOT EXISTS so re-apply is safe.)

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (filename) VALUES
    ('001_init.sql'),
    ('002_sentences.sql'),
    ('003_document_entity.sql'),
    ('004_normalize_4nf.sql'),
    ('005_lexemes.sql'),
    ('006_schema_migrations.sql')
ON CONFLICT (filename) DO NOTHING;
