-- doc-ray: the remissive index — every surface form -> its canonical entry
-- (entity-canonicalization layer, D-017 / D-019). See docs/canonicalization_pipeline.md.
-- Works both directions: form_norm -> canonical (the resolver / Stage-2 first pass)
-- and canonical_id -> all forms (the índice remissivo cross-reference view).
--
-- Deviation from the design doc: `lang` is NOT NULL DEFAULT '' because it is part of
-- the primary key (a NULL key column is not allowed); '' means "language-agnostic".

CREATE TABLE IF NOT EXISTS form_index (
    form_norm    text   NOT NULL,                    -- normalized lookup key (NFC+casefold+translit)
    form_surface text   NOT NULL,                    -- as seen: 'Zelenskyy' · 'the president'
    canonical_id bigint NOT NULL REFERENCES canonical(id) ON DELETE CASCADE,
    lang         text   NOT NULL DEFAULT '',
    form_type    text   NOT NULL,                    -- name|alias|acronym|epithet|translit|descriptor|misspelling
    confidence   real   NOT NULL DEFAULT 1.0,
    occurrences  int    NOT NULL DEFAULT 0,          -- corpus frequency of this exact form
    source_types text[] NOT NULL DEFAULT '{}',       -- {wikipedia,news,tweet,book}
    first_seen   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (form_norm, canonical_id, lang)
);

CREATE INDEX IF NOT EXISTS idx_form_lookup       ON form_index (form_norm);     -- form -> canonical
CREATE INDEX IF NOT EXISTS idx_form_by_canonical ON form_index (canonical_id);  -- canonical -> forms

INSERT INTO schema_migrations (filename) VALUES ('022_form_index.sql')
    ON CONFLICT (filename) DO NOTHING;
