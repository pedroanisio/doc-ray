-- doc-ray: normalize the schema to 4NF.
--
-- 4NF forbids non-trivial multivalued dependencies on a non-superkey. The
-- entity carried three independent multivalued facts (authors, tags,
-- identifiers) plus two attribute bags (metadata, provenance params) as
-- arrays/jsonb — none of which are 1NF, let alone 4NF. Each becomes its own
-- relation whose key functionally determines a single-valued attribute.

-- --- authors: dimension + junction (author ⋈ document is an independent MVD) ---
CREATE TABLE IF NOT EXISTS authors (
    id   bigserial PRIMARY KEY,
    name text NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS document_authors (
    document_id uuid   NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    author_id   bigint NOT NULL REFERENCES authors(id),
    position    int    NOT NULL DEFAULT 0,     -- author order on the document
    PRIMARY KEY (document_id, author_id)
);
CREATE INDEX IF NOT EXISTS idx_document_authors_author ON document_authors (author_id);

-- --- tags: dimension + junction (independent of authors -> separate relation) ---
CREATE TABLE IF NOT EXISTS tags (
    id  bigserial PRIMARY KEY,
    tag text NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS document_tags (
    document_id uuid   NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id      bigint NOT NULL REFERENCES tags(id),
    PRIMARY KEY (document_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_document_tags_tag ON document_tags (tag_id);

-- --- identifiers: (document, scheme) -> value  (doi / isbn / url / ...) --------
CREATE TABLE IF NOT EXISTS document_identifiers (
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    scheme      text NOT NULL,
    value       text NOT NULL,
    PRIMARY KEY (document_id, scheme, value)
);

-- --- metadata: (document, key) -> value  (format-specific attributes) ----------
CREATE TABLE IF NOT EXISTS document_metadata (
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    key         text NOT NULL,
    value       text NOT NULL,
    PRIMARY KEY (document_id, key)
);

-- --- provenance params: (event, key) -> value ---------------------------------
CREATE TABLE IF NOT EXISTS provenance_params (
    event_id ulid NOT NULL REFERENCES document_provenance(id) ON DELETE CASCADE,
    key      text NOT NULL,
    value    text NOT NULL,
    PRIMARY KEY (event_id, key)
);

-- Promote the one genuinely single-valued source attribute out of metadata.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS original_filename text;

-- =========================== migrate existing data ===========================

INSERT INTO authors (name)
    SELECT DISTINCT a FROM documents, LATERAL unnest(authors) AS a
    WHERE a IS NOT NULL
    ON CONFLICT (name) DO NOTHING;

INSERT INTO document_authors (document_id, author_id, position)
    SELECT d.id, au.id, x.ord - 1
    FROM documents d
    CROSS JOIN LATERAL unnest(d.authors) WITH ORDINALITY AS x(name, ord)
    JOIN authors au ON au.name = x.name
    ON CONFLICT DO NOTHING;

INSERT INTO tags (tag)
    SELECT DISTINCT t FROM documents, LATERAL unnest(tags) AS t
    WHERE t IS NOT NULL
    ON CONFLICT (tag) DO NOTHING;

INSERT INTO document_tags (document_id, tag_id)
    SELECT d.id, tg.id
    FROM documents d
    CROSS JOIN LATERAL unnest(d.tags) AS t
    JOIN tags tg ON tg.tag = t
    ON CONFLICT DO NOTHING;

INSERT INTO document_identifiers (document_id, scheme, value)
    SELECT d.id, e.key, e.value
    FROM documents d, LATERAL jsonb_each_text(d.identifiers) AS e
    WHERE e.value IS NOT NULL
    ON CONFLICT DO NOTHING;

UPDATE documents
   SET original_filename = metadata->>'source_filename'
 WHERE metadata ? 'source_filename';

INSERT INTO document_metadata (document_id, key, value)
    SELECT d.id, e.key, e.value
    FROM documents d, LATERAL jsonb_each_text(d.metadata) AS e
    WHERE e.key <> 'source_filename' AND e.value IS NOT NULL
    ON CONFLICT DO NOTHING;

INSERT INTO provenance_params (event_id, key, value)
    SELECT p.id, e.key, e.value
    FROM document_provenance p, LATERAL jsonb_each_text(p.params) AS e
    WHERE e.value IS NOT NULL
    ON CONFLICT DO NOTHING;

-- =========================== drop denormalized columns =======================

ALTER TABLE documents           DROP COLUMN IF EXISTS authors;
ALTER TABLE documents           DROP COLUMN IF EXISTS tags;
ALTER TABLE documents           DROP COLUMN IF EXISTS identifiers;
ALTER TABLE documents           DROP COLUMN IF EXISTS metadata;
ALTER TABLE document_provenance DROP COLUMN IF EXISTS params;
