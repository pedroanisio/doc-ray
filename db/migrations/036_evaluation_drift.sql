-- doc-ray: evaluation and corpus-drift artifacts.
--
-- These tables make NLP quality measurements durable instead of implicit in
-- one-off health reports. `corpus_snapshot` captures source facts;
-- `evaluation_run` identifies the evaluator/tool that interpreted those facts;
-- `evaluation_metric` stores one metric per subject with citations back to the
-- snapshot facts; `gold_sample` provides first-class labeled examples for
-- future model/tool comparisons.

CREATE TABLE IF NOT EXISTS corpus_snapshot (
    id                      ulid PRIMARY KEY,
    created_at              timestamptz NOT NULL DEFAULT now(),
    source                  text NOT NULL,
    schema_version           text NOT NULL,
    document_count           bigint NOT NULL,
    sentence_count           bigint NOT NULL,
    token_count              bigint NOT NULL,
    lexeme_count             bigint NOT NULL,
    lexeme_occurrence_count  bigint NOT NULL,
    entity_mention_count     bigint NOT NULL,
    entity_link_count        bigint NOT NULL,
    review_pending_count     bigint NOT NULL DEFAULT 0,
    review_approved_count    bigint NOT NULL DEFAULT 0,
    review_rejected_count    bigint NOT NULL DEFAULT 0,
    tool_versions            jsonb NOT NULL DEFAULT '{}'::jsonb,
    lexeme_distribution      jsonb NOT NULL DEFAULT '{}'::jsonb,
    entity_distribution      jsonb NOT NULL DEFAULT '{}'::jsonb,
    fact_refs                jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_corpus_snapshot_created
    ON corpus_snapshot (created_at DESC);

CREATE TABLE IF NOT EXISTS evaluation_run (
    id                ulid PRIMARY KEY,
    snapshot_id       ulid NOT NULL REFERENCES corpus_snapshot(id) ON DELETE CASCADE,
    created_at        timestamptz NOT NULL DEFAULT now(),
    evaluator         text NOT NULL,
    evaluator_version text NOT NULL,
    schema_version    text NOT NULL,
    note              text
);

CREATE INDEX IF NOT EXISTS idx_evaluation_run_snapshot
    ON evaluation_run (snapshot_id, created_at DESC);

CREATE TABLE IF NOT EXISTS evaluation_metric (
    run_id        ulid NOT NULL REFERENCES evaluation_run(id) ON DELETE CASCADE,
    name          text NOT NULL,
    subject       text NOT NULL,
    subject_id    text NOT NULL,
    value_numeric double precision,
    value_json    jsonb NOT NULL DEFAULT '{}'::jsonb,
    unit          text NOT NULL DEFAULT 'count',
    fact_refs     jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, name, subject, subject_id)
);

CREATE INDEX IF NOT EXISTS idx_evaluation_metric_name
    ON evaluation_metric (name, subject, subject_id);

CREATE TABLE IF NOT EXISTS gold_sample (
    id          ulid PRIMARY KEY,
    document_id uuid REFERENCES documents(id) ON DELETE CASCADE,
    span_id     uuid REFERENCES spans(id) ON DELETE CASCADE,
    label       text NOT NULL,
    expected    jsonb NOT NULL,
    source      text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gold_sample_label
    ON gold_sample (label, created_at DESC);

GRANT SELECT ON corpus_snapshot, evaluation_run, evaluation_metric, gold_sample
    TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('036_evaluation_drift.sql')
    ON CONFLICT (filename) DO NOTHING;
