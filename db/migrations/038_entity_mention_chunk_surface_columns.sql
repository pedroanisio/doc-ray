-- doc-ray: add (nullable) surface columns to entity_mention and chunk.
--
-- Split from the backfill (039/040) and the NOT NULL/index finalization (041)
-- so each phase is its own transaction. A single combined migration held an
-- AccessExclusiveLock (from these ADD COLUMN statements) for the entire
-- multi-hour backfill, blocking every other reader and writer of these tables
-- for the whole run — including the annotation worker's own COPY writes. This
-- phase alone is a fast, metadata-only DDL change.

ALTER TABLE entity_mention ADD COLUMN IF NOT EXISTS surface text;
ALTER TABLE chunk         ADD COLUMN IF NOT EXISTS surface text;

INSERT INTO schema_migrations (filename) VALUES ('038_entity_mention_chunk_surface_columns.sql')
    ON CONFLICT (filename) DO NOTHING;
