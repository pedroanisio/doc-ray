-- doc-ray: make full-text search language-aware (review fix #5).
--
-- 001 defined document_content.fts as a GENERATED column hardcoded to the
-- 'english' text-search config. The corpus is bilingual (en + pt), so Portuguese
-- documents were stemmed with English rules. A generated column can only see its
-- own row, and `to_tsvector(regconfig_column, text)` is STABLE (not IMMUTABLE),
-- so per-row config is impossible in a generated column. We convert fts to a
-- plain tsvector maintained by triggers that resolve the document's language.
--
-- documents.language is populated AFTER the content row is inserted (the
-- annotator detects it), so a second trigger recomputes fts when language lands.

-- lang code / name -> Postgres text-search config. Unknown -> 'simple' (no
-- stemming, no stopwords) so we never mis-stem rather than guess.
CREATE OR REPLACE FUNCTION docray_ts_config(lang text) RETURNS regconfig AS $$
  SELECT CASE lower(coalesce(lang, ''))
           WHEN 'en'         THEN 'english'
           WHEN 'eng'        THEN 'english'
           WHEN 'english'    THEN 'english'
           WHEN 'pt'         THEN 'portuguese'
           WHEN 'por'        THEN 'portuguese'
           WHEN 'portuguese' THEN 'portuguese'
           ELSE 'simple'
         END::regconfig
$$ LANGUAGE sql IMMUTABLE;

-- Convert fts from GENERATED to a plain, trigger-maintained tsvector.
-- (DROP COLUMN also drops the dependent idx_content_fts; recreated below.)
-- Idempotent: only convert while it is still a generated column.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'document_content' AND column_name = 'fts'
                 AND is_generated = 'ALWAYS') THEN
        ALTER TABLE document_content DROP COLUMN fts;
        ALTER TABLE document_content ADD COLUMN fts tsvector;
    END IF;
END $$;

-- Compute fts on content write, using the owning document's current language.
CREATE OR REPLACE FUNCTION set_document_content_fts() RETURNS trigger AS $$
DECLARE
    lang text;
BEGIN
    SELECT d.language INTO lang FROM documents d WHERE d.id = NEW.document_id;
    NEW.fts := to_tsvector(docray_ts_config(lang), coalesce(NEW.full_text, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_content_fts ON document_content;
CREATE TRIGGER trg_content_fts
    BEFORE INSERT OR UPDATE OF full_text ON document_content
    FOR EACH ROW EXECUTE FUNCTION set_document_content_fts();

-- Language is set on the document after the content exists — reindex on change.
-- (Sets only fts, so it does not re-fire the full_text trigger above.)
CREATE OR REPLACE FUNCTION refresh_content_fts_on_language() RETURNS trigger AS $$
BEGIN
    UPDATE document_content
       SET fts = to_tsvector(docray_ts_config(NEW.language), coalesce(full_text, ''))
     WHERE document_id = NEW.id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_documents_language_fts ON documents;
CREATE TRIGGER trg_documents_language_fts
    AFTER UPDATE OF language ON documents
    FOR EACH ROW EXECUTE FUNCTION refresh_content_fts_on_language();

-- Backfill every existing row with its language-correct vector.
UPDATE document_content c
   SET fts = to_tsvector(docray_ts_config(d.language), coalesce(c.full_text, ''))
  FROM documents d
 WHERE d.id = c.document_id;

CREATE INDEX IF NOT EXISTS idx_content_fts ON document_content USING gin (fts);

INSERT INTO schema_migrations (filename) VALUES ('016_fts_language_aware.sql')
    ON CONFLICT (filename) DO NOTHING;
