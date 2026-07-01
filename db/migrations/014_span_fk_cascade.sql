-- doc-ray: make every span reference ON DELETE CASCADE.
--
-- Spans are owned by a document; deleting a document cascades to its spans, so
-- every table that references spans(id) must cascade too — otherwise a document
-- delete is blocked. (embedding.span_id already had CASCADE.)

DO $$
DECLARE
    fk record;
BEGIN
    FOR fk IN
        SELECT con.conname, rel.relname AS table_name, att.attname AS column_name
        FROM pg_constraint con
        JOIN pg_class rel        ON rel.oid = con.conrelid
        JOIN pg_class frel       ON frel.oid = con.confrelid
        JOIN pg_attribute att    ON att.attrelid = con.conrelid
                                 AND att.attnum = con.conkey[1]
        WHERE con.contype = 'f'
          AND frel.relname = 'spans'
          AND con.confdeltype <> 'c'          -- not already ON DELETE CASCADE
    LOOP
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', fk.table_name, fk.conname);
        EXECUTE format(
            'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (%I) '
            'REFERENCES spans(id) ON DELETE CASCADE',
            fk.table_name, fk.conname, fk.column_name);
        RAISE NOTICE 'cascaded % (%.%)', fk.conname, fk.table_name, fk.column_name;
    END LOOP;
END $$;

INSERT INTO schema_migrations (filename) VALUES ('014_span_fk_cascade.sql')
    ON CONFLICT (filename) DO NOTHING;
