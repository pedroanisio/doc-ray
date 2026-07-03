-- doc-ray: backup catalog. One row per backup archive produced by the backup
-- engine (src/doc_ray/application/backup.py), driven by the KIND_BACKUP worker
-- job. The archive itself (a .tar.gz of the logical DB dump + object-store blobs)
-- lives in the object store under `backups/<id>.tar.gz`; this table is the index.
--
-- This table and `jobs` are the only base tables EXCLUDED from a backup: they are
-- operational/meta state, not corpus data, so a restore must not clobber the
-- backup catalog it is restoring from or the live job queue.

CREATE TABLE IF NOT EXISTS backups (
    id             ulid PRIMARY KEY,
    created_at     timestamptz NOT NULL DEFAULT now(),
    status         text NOT NULL DEFAULT 'creating'
                     CHECK (status IN ('creating', 'available', 'restoring', 'failed')),
    object_key     text,               -- object-store key of the archive
    schema_version text,               -- docray_schema_version() captured at backup time
    archive_bytes  bigint,
    table_rows     bigint,             -- total data rows captured across all tables
    blob_count     integer,
    blob_bytes     bigint,
    doc_count      integer,            -- documents in the snapshot (human-facing summary)
    note           text,
    error          text,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_backups_created ON backups (created_at DESC);

-- Keep updated_at fresh (reuses the trigger function from 001_init).
DROP TRIGGER IF EXISTS trg_backups_updated_at ON backups;
CREATE TRIGGER trg_backups_updated_at
    BEFORE UPDATE ON backups
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Admin metadata (not document-scoped); expose read to the RLS client role so the
-- webapp's /backups page works whether or not it runs as docray_client.
GRANT SELECT ON backups TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('026_backups.sql')
    ON CONFLICT (filename) DO NOTHING;
