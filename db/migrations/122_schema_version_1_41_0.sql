-- doc-ray: schema SemVer 1.41.0 (Lens Registry; migration 121).
--
-- Additive: is_scope / parent_lens_id on document_collections, default_lens_id
-- on documents, plus the integrity triggers and the reserved `global` lens.
-- No existing column changed, so this is a MINOR bump over 1.40.0.

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.41.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('122_schema_version_1_41_0.sql')
ON CONFLICT (filename) DO NOTHING;
