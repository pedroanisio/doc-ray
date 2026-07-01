-- doc-ray sentence layer: every sentence stored as its own row, keyed by ULID.

-- A ULID is exactly 26 Crockford-base32 chars; the leading char is 0-7 because
-- the 48-bit timestamp leaves the top 2 bits of the 130-bit encoding as zero.
-- Storing the canonical string (not a raw uuid) keeps ORDER BY id == time order.
DO $$
BEGIN
    CREATE DOMAIN ulid AS char(26)
        CHECK (VALUE ~ '^[0-7][0-9A-HJKMNP-TV-Z]{25}$');
EXCEPTION
    WHEN duplicate_object THEN NULL;   -- idempotent re-apply
END $$;

CREATE TABLE IF NOT EXISTS sentences (
    id           ulid PRIMARY KEY,
    document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal      int  NOT NULL,                 -- reading order within the doc
    text         text NOT NULL,
    char_start   int  NOT NULL,                 -- span into document_content.full_text
    char_end     int  NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, ordinal),
    CHECK (char_end >= char_start)
);

-- Reading-order scan within a document.
CREATE INDEX IF NOT EXISTS idx_sentences_document
    ON sentences (document_id, ordinal);

-- Trigram search over sentence text (ad-hoc lookups); pg_trgm loaded in 001.
CREATE INDEX IF NOT EXISTS idx_sentences_text_trgm
    ON sentences USING gin (text gin_trgm_ops);
