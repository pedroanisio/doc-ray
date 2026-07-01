-- doc-ray: meaning, discourse, temporal, sentiment (NLP layers 14-19).

-- 14 · semantic role labeling: predicate 1:N argument
CREATE TABLE IF NOT EXISTS srl_predicate (
    id            ulid PRIMARY KEY,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    token_id      ulid NOT NULL REFERENCES tokens(id),
    frame         text NOT NULL                    -- 'found.01'
);
CREATE TABLE IF NOT EXISTS srl_argument (
    predicate_id ulid NOT NULL REFERENCES srl_predicate(id) ON DELETE CASCADE,
    span_id      uuid NOT NULL REFERENCES spans(id),
    role         text NOT NULL,                     -- ARG0 / ARG1 / ARGM-LOC
    PRIMARY KEY (predicate_id, span_id)
);

-- 15 · word sense disambiguation
CREATE TABLE IF NOT EXISTS word_sense (
    token_id      ulid NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    sense_key     text NOT NULL,                    -- WordNet sense key
    PRIMARY KEY (token_id, annotation_id)
);

-- 16 · AMR: rooted, reentrant meaning graph (DAG) -> node + edge
CREATE TABLE IF NOT EXISTS amr_node (
    id            ulid PRIMARY KEY,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    sentence_id   ulid REFERENCES sentences(id),
    concept       text NOT NULL
);
CREATE TABLE IF NOT EXISTS amr_edge (
    id             ulid PRIMARY KEY,
    parent_node_id ulid NOT NULL REFERENCES amr_node(id) ON DELETE CASCADE,
    child_node_id  ulid NOT NULL REFERENCES amr_node(id),
    role           text NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_amr_edge_child ON amr_edge (child_node_id);

-- 17 · discourse (PDTB): relation between two argument spans
CREATE TABLE IF NOT EXISTS discourse_relation (
    id                 ulid PRIMARY KEY,
    annotation_id      ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    connective_span_id uuid REFERENCES spans(id),
    arg1_span_id       uuid NOT NULL REFERENCES spans(id),
    arg2_span_id       uuid NOT NULL REFERENCES spans(id),
    sense              text NOT NULL
);

-- 18 · TimeML: TIMEX3 + EVENT + TLINK
CREATE TABLE IF NOT EXISTS timex (
    id            ulid PRIMARY KEY,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    span_id       uuid NOT NULL REFERENCES spans(id),
    timex_type    text NOT NULL,                    -- DATE / TIME / DURATION
    value         text
);
CREATE TABLE IF NOT EXISTS event (
    id            ulid PRIMARY KEY,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    token_id      ulid NOT NULL REFERENCES tokens(id),
    event_class   text
);
CREATE TABLE IF NOT EXISTS tlink (                   -- orders two temporal anchors
    id            ulid PRIMARY KEY,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    source_id     ulid NOT NULL,                     -- event or timex id (polymorphic)
    target_id     ulid NOT NULL,
    relation      text NOT NULL                      -- BEFORE / AFTER / IS_INCLUDED
);

-- 19 · sentiment & pragmatics
CREATE TABLE IF NOT EXISTS sentiment (
    id             ulid PRIMARY KEY,
    annotation_id  ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    span_id        uuid NOT NULL REFERENCES spans(id),
    polarity       text NOT NULL,                    -- POSITIVE / NEGATIVE / NEUTRAL
    score          real,
    target_span_id uuid REFERENCES spans(id)
);

INSERT INTO schema_migrations (filename) VALUES ('011_meaning_discourse.sql')
    ON CONFLICT (filename) DO NOTHING;
