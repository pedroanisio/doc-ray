-- doc-ray: finalize entity_mention.surface / chunk.surface after backfill.
--
-- Runs only after both backfills (039, 040) have populated every existing
-- row, so the NOT NULL checks are a fast validation scan rather than a
-- rewrite. The AccessExclusiveLock these ALTER COLUMN/CREATE INDEX
-- statements take is brief by comparison — the expensive part already ran,
-- unlocked, in the two prior migrations.

ALTER TABLE entity_mention ALTER COLUMN surface SET NOT NULL;
ALTER TABLE chunk         ALTER COLUMN surface SET NOT NULL;

-- Serves entity_frequency's GROUP BY surface, entity_type and the entityType
-- filter on list_entities/entityFrequency without touching document_content.
CREATE INDEX IF NOT EXISTS idx_entity_mention_type_surface ON entity_mention (entity_type, surface);

INSERT INTO schema_migrations (filename) VALUES ('041_entity_mention_chunk_surface_finalize.sql')
    ON CONFLICT (filename) DO NOTHING;
