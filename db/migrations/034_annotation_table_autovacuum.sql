-- doc-ray: proactive autovacuum tuning for high-churn annotation tables.
--
-- Reannotation deletes and rewrites annotation-layer rows by document. The
-- largest tables on that path need table-local autovacuum settings instead of
-- inheriting broad cluster defaults.

ALTER TABLE tokens SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_vacuum_threshold = 1000,
    autovacuum_analyze_scale_factor = 0.01,
    autovacuum_analyze_threshold = 1000
);

ALTER TABLE dependency SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_vacuum_threshold = 1000,
    autovacuum_analyze_scale_factor = 0.01,
    autovacuum_analyze_threshold = 1000
);

ALTER TABLE lexeme_occurrences SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_vacuum_threshold = 1000,
    autovacuum_analyze_scale_factor = 0.01,
    autovacuum_analyze_threshold = 1000
);

ALTER TABLE entity_mention SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_vacuum_threshold = 1000,
    autovacuum_analyze_scale_factor = 0.01,
    autovacuum_analyze_threshold = 1000
);

INSERT INTO schema_migrations (filename) VALUES ('034_annotation_table_autovacuum.sql')
    ON CONFLICT (filename) DO NOTHING;
