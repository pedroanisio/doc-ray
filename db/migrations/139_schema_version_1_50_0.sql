-- doc-ray: schema SemVer 1.50.0 — explainable NER mention-confidence review.
--
-- Migration 138 adds nullable, worker-owned confidence materialization to the
-- existing entity_mention fact table. This additive schema capability is a
-- MINOR release and stays in lockstep with the package, changelog, and
-- engineering-decisions source version.

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.50.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('139_schema_version_1_50_0.sql')
ON CONFLICT (filename) DO NOTHING;
