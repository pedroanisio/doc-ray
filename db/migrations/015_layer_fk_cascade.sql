-- doc-ray: complete the ON DELETE graph for document-owned annotation tables.
--
-- Every FK that references a document-scoped table (tokens, amr_node, …) must
-- cascade (or SET NULL for a nullable advisory link) so a document delete flows
-- all the way through. FKs to GLOBAL dimensions (lexemes, authors, tags,
-- documents.derived_from) are intentionally left NO ACTION.

DO $$
DECLARE
    owned text[] := ARRAY['spans','sentences','tokens','amr_node','entity_mention',
                          'srl_predicate','coref_chain','constituent'];
    fk record;
    action text;
BEGIN
    FOR fk IN
        SELECT con.conname, rel.relname AS table_name, frel.relname AS ref_table,
               att.attname AS column_name, att.attnotnull
        FROM pg_constraint con
        JOIN pg_class rel     ON rel.oid = con.conrelid
        JOIN pg_class frel    ON frel.oid = con.confrelid
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1]
        WHERE con.contype = 'f'
          AND con.confdeltype = 'a'                 -- currently NO ACTION
          AND frel.relname = ANY(owned)
    LOOP
        action := CASE WHEN fk.attnotnull THEN 'CASCADE' ELSE 'SET NULL' END;
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', fk.table_name, fk.conname);
        EXECUTE format(
            'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I(id) ON DELETE %s',
            fk.table_name, fk.conname, fk.column_name, fk.ref_table, action);
        RAISE NOTICE 'fixed %.% -> %.% ON DELETE %',
            fk.table_name, fk.column_name, fk.ref_table, 'id', action;
    END LOOP;
END $$;

INSERT INTO schema_migrations (filename) VALUES ('015_layer_fk_cascade.sql')
    ON CONFLICT (filename) DO NOTHING;
