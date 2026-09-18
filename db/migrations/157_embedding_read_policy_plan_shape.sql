-- doc-ray: make the embedding read policy executable at corpus scale (BL-205).
--
-- `semantic_search_sentences` timed out on EVERY corpus-wide query while the
-- same call scoped to one `document_id` returned promptly. The vector index was
-- never the problem; the row-level security policy's SHAPE was:
--
--     span_id IN (SELECT spans.id FROM spans
--                  WHERE spans.document_id IN (SELECT docray_readable_documents()))
--
-- Measured with EXPLAIN as `docray_api` with a principal on a large corpus: the
-- planner estimates that IN-list at tens of millions of span ids — a quarter of
-- a `spans` table with >100M rows — which is far past `work_mem`, so it cannot build
-- a hash and falls back to
--
--     SubPlan 2 -> Materialize (rows=28242348)
--
-- scanned LINEARLY for every candidate row the HNSW index produces. The same
-- EXPLAIN shows the equivalent predicate on `sentences` as `hashed SubPlan`,
-- evaluated once, because that table carries `document_id` directly and needs no
-- detour through `spans`. That contrast is the whole diagnosis.
--
-- WHY NOT DENORMALISE document_id ONTO embedding
-- It would produce the same cheap plan, and it was the first idea. It also means
-- UPDATEing every one of ~3.4M rows of an HNSW-indexed table — precisely the
-- write amplification `application/semantic_embed.py` is built to avoid ("COPY,
-- never per-row INSERT") — bloating the vector index for a read-path fix. The
-- policy rewrite reaches the same plan with no table rewrite at all.
--
-- WHY THIS IS THE SAME POLICY
-- `spans.id` is the primary key, so `span_id IN (SELECT id FROM spans WHERE P)`
-- and `EXISTS (SELECT 1 FROM spans WHERE id = span_id AND P)` admit exactly the
-- same rows: uniqueness makes the semi-join and the existence test equivalent,
-- and `embedding.span_id` is NOT NULL so there is no three-valued-logic corner.
-- Visibility is unchanged; only the plan changes. The behavioural proof is
-- src/doc_ray/tests/test_semantic_search_rls.py, which asserts both directions
-- (unreadable stays hidden, readable stays visible) rather than trusting this
-- argument.
--
-- WHY A SCALAR SUBQUERY AND NOT EXISTS
-- EXISTS is the obvious rewrite and it is NOT sufficient — measured, still >90 s.
-- The planner un-nests a correlated EXISTS back into a semi-join and re-derives
-- the entire readable span set in order to hash it:
--
--     Filter: (ANY (span_id = (hashed SubPlan 6).col1))
--     SubPlan 6 -> Index Scan on spans (document_id = ANY ((InitPlan 4).col1))
--
-- Hashing ~28M span ids beats materialising them, but it is still O(corpus) on
-- every query, which is the thing being removed. A correlated SCALAR subquery
-- cannot be flattened into a semi-join: it has to be evaluated per outer row,
-- which is precisely the single primary-key probe wanted here. The planner is
-- being constrained deliberately, not decorated.
--
-- WHY ARRAY(...) AROUND THE READABLE SET
-- `docray_readable_documents()` returns SETOF and is STABLE, but STABLE does not
-- mean memoised: inside a correlated subquery the planner will happily re-run it
-- per candidate row (seen as `ProjectSet ... loops=20419`). ARRAY(SELECT ...) is
-- uncorrelated, so it collapses to an InitPlan evaluated ONCE per statement. The
-- function itself is cheap (787 rows in 0.01 s here); it is the *number of
-- calls* that mattered.
--
-- NULL SEMANTICS. If `span_id` has no row in `spans` the scalar subquery yields
-- NULL, `NULL = ANY (...)` is NULL, and the row is not visible — the same outcome
-- the IN-list form produced for an orphan span. `embedding.span_id` is NOT NULL,
-- so there is no other three-valued corner.
--
-- Measured on the same corpus, same query, `docray_api` + principal:
--     old IN-list policy      >90 s (statement timeout; the reported bug)
--     EXISTS rewrite          >90 s (semi-join, hashed 28M span ids)
--     scalar subquery          0.20 s
--
-- Policy-only: no table, column, index or function is altered, so this applies
-- instantly and rolls back by restoring the previous policy body.

DROP POLICY IF EXISTS p_read ON embedding;

CREATE POLICY p_read ON embedding
    FOR SELECT
    USING (
        (SELECT sp.document_id FROM spans sp WHERE sp.id = embedding.span_id)
            = ANY (ARRAY(SELECT docray_readable_documents()))
    );

COMMENT ON TABLE embedding IS
    'Learned sentence vectors (NLP Layer 20), one row per span per model. Read '
    'visibility is inherited from the owning document through spans; the policy '
    'probes spans by primary key against a once-materialised readable-document '
    'array, because the set-of-span-ids form could not be hashed at this '
    'cardinality and was scanned per candidate row (BL-205).';

INSERT INTO schema_migrations (filename)
VALUES ('157_embedding_read_policy_plan_shape.sql')
ON CONFLICT (filename) DO NOTHING;
