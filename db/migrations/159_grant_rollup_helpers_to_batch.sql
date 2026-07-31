-- doc-ray: let the rollup driver actually call its helpers (repairs migration 154).
--
-- Migration 154 created the per-document rollup helpers
--
--     docray_refresh_document_lexeme_stats(uuid)
--     docray_refresh_document_entity_frequency_stats(uuid)
--     docray_unapply_document_rollups(uuid)
--
-- owned by `docray`, then REVOKEd ALL from PUBLIC — and never granted EXECUTE to
-- `docray_batch`. Their only caller, `docray_refresh_stats_for_document`, is
-- SECURITY DEFINER owned by `docray_batch`, so its body runs AS docray_batch and
-- cannot execute them. Reproduced on production:
--
--     InsufficientPrivilege: permission denied for function
--         docray_refresh_document_lexeme_stats
--     CONTEXT: SQL statement "SELECT docray_refresh_document_lexeme_stats(doc)"
--              PL/pgSQL function docray_refresh_stats_for_document(uuid,interval) line 17
--
-- It fails for EVERY role, `docray` included, because the outer function switches
-- identity before it reaches the inner call — which is why it is not visible as a
-- permissions problem from any particular caller's point of view. Migration 154's
-- own comment states the intent ("they run as docray_batch (BYPASSRLS)"), so the
-- grant was simply omitted.
--
-- CONSEQUENCE. Every per-document rollup refresh failed, on every document, since
-- 154 applied. `lexeme_stats`, `entity_frequency_stats` and the `corpus_stats`
-- totals derived from them stopped tracking the corpus: the delta path 154
-- introduced replaced the full rebuild, so nothing else was maintaining them.
-- The failure was logged per document rather than raised, which is why ingestion
-- kept running while the read model silently went stale.
--
-- Idempotent: GRANT is a no-op when already held.

GRANT EXECUTE ON FUNCTION docray_refresh_document_lexeme_stats(uuid) TO docray_batch;
GRANT EXECUTE ON FUNCTION docray_refresh_document_entity_frequency_stats(uuid) TO docray_batch;
GRANT EXECUTE ON FUNCTION docray_unapply_document_rollups(uuid) TO docray_batch;

INSERT INTO schema_migrations (filename)
VALUES ('159_grant_rollup_helpers_to_batch.sql')
ON CONFLICT (filename) DO NOTHING;
