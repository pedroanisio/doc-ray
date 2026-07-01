-- doc-ray: promote `documents` to a first-class entity (bibliographic identity,
-- content/source identity, lineage) and add an append-only provenance log.

-- --- documents: bibliographic identity -------------------------------------
ALTER TABLE documents ADD COLUMN IF NOT EXISTS subtitle      text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS authors       text[] NOT NULL DEFAULT '{}';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS language      text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS publisher     text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS published_on  date;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS identifiers   jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS license       text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tags          text[] NOT NULL DEFAULT '{}';

-- --- documents: content / source identity ----------------------------------
ALTER TABLE documents ADD COLUMN IF NOT EXISTS mime_type     text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS char_count    int;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS text_sha256   char(64);

-- --- documents: lineage ----------------------------------------------------
ALTER TABLE documents ADD COLUMN IF NOT EXISTS derived_from  uuid REFERENCES documents(id);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS revision      int NOT NULL DEFAULT 1;

-- Migrate the legacy single `author` into the authors[] array, then retire it.
UPDATE documents
   SET authors = ARRAY[author]
 WHERE author IS NOT NULL
   AND (authors IS NULL OR authors = '{}');
ALTER TABLE documents DROP COLUMN IF EXISTS author;

-- --- provenance: append-only processing/lineage log ------------------------
DO $$
BEGIN
    CREATE TYPE provenance_kind AS ENUM (
        'acquired', 'stored', 'extracted', 'decomposed', 'segmented',
        'embedded', 'indexed', 'redacted', 'translated', 'superseded'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;   -- idempotent re-apply
END $$;

CREATE TABLE IF NOT EXISTS document_provenance (
    id            ulid PRIMARY KEY,                 -- ULID => time-ordered event id
    document_id   uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind          provenance_kind NOT NULL,
    tool          text,                             -- 'pypdf' | 'object-store' | ...
    tool_version  text,
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    actor         text,                             -- user / service / run id
    input_sha256  char(64),
    output_sha256 char(64),
    params        jsonb NOT NULL DEFAULT '{}'::jsonb,
    note          text
);

-- Reconstruct a document's history in event order (ULID sort == time order).
CREATE INDEX IF NOT EXISTS idx_provenance_doc
    ON document_provenance (document_id, id);
CREATE INDEX IF NOT EXISTS idx_provenance_kind
    ON document_provenance (kind);
