-- doc-ray: schema SemVer 1.57.3 — Layer 9 stops deleting a phrase's own head.
--
-- No DDL. `domain/syntax_derived.derive_constituents` built two constituents by
-- removing a node's dependents from its subtree, matching them by head index —
-- a test a self-headed token passes vacuously, so the projecting token could be
-- filtered out of the phrase it heads. Where dependents remained, the node kept
-- a span that omitted its own head; where none did, the span had nothing to
-- measure and `annotate` failed terminally with `ValueError: min() iterable
-- argument is empty`, stranding four documents at `status='extracted'` with the
-- rest of their pipeline chain never enqueued.
--
-- The version moves because Layer 9 constituency rows written before this fix
-- may carry understated spans: `constituents.char_start` can begin after the
-- head token of the phrase it describes. Consumers reading those offsets are
-- reading a schema-level contract, so the correction is versioned even though
-- no table, column, policy or function changes. Re-annotating a document
-- rewrites its Layer 9 rows under the same annotation record.
--
-- PATCH, not minor: no capability is gained or lost. Layer 9 was always
-- specified to project a phrase over the subtree its head governs; it simply
-- did not include that head in every case, and now does.
--
-- Keeps doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md and the
-- database in lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.57.3')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('161_schema_version_1_57_3.sql')
ON CONFLICT (filename) DO NOTHING;
