-- doc-ray: schema SemVer 1.52.0 — the measured phrase pre-flight (#39).
--
-- Pairs with 142, which added corpus_stats.n_sentences. Release 1.52.0 makes
-- term_frequency answer multi-word terms with a bounded, measured sentence-hit
-- verdict under the same literal-substring rule search_sentences applies, instead
-- of a component upper bound that left adjacency unproven. This marker keeps
-- doc_ray.__version__, engineering_decisions.yml, CHANGELOG.md, and the database
-- in lockstep (scripts/check_version_consistency.py).

INSERT INTO schema_version (singleton, version)
VALUES (true, '1.52.0')
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = now();

INSERT INTO schema_migrations (filename)
VALUES ('143_schema_version_1_52_0.sql')
ON CONFLICT (filename) DO NOTHING;
