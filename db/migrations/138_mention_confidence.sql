-- mention confidence — materialized, explainable confidence for NER mention review.
--
-- spaCy's default transition-based NER exposes no calibrated entity probability.
-- The worker therefore writes a deterministic heuristic score plus its reasons
-- onto the ephemeral entity_mention row. The value is a triage rank, not a
-- calibrated probability: clients order it ascending to review the least-trusted
-- mentions first and must display confidence_reasons beside it.
--
-- entity_mention remains worker-owned. Existing API/client grants are unchanged:
-- request paths read these derived facts but never write them.

ALTER TABLE entity_mention
    ADD COLUMN IF NOT EXISTS confidence real;
ALTER TABLE entity_mention
    ADD COLUMN IF NOT EXISTS confidence_method text;
ALTER TABLE entity_mention
    ADD COLUMN IF NOT EXISTS confidence_reasons text[] NOT NULL DEFAULT '{}'::text[];

DO $$
BEGIN
    ALTER TABLE entity_mention
        ADD CONSTRAINT entity_mention_confidence_range
        CHECK (confidence >= 0 AND confidence <= 1);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN entity_mention.confidence IS
    'Explainable review triage rank, not a calibrated probability; lower is reviewed first.';
COMMENT ON COLUMN entity_mention.confidence_method IS
    'Producer of confidence, currently heuristic:v1; reserved for future model:<name> scores.';
COMMENT ON COLUMN entity_mention.confidence_reasons IS
    'Deterministic human-readable reasons that must accompany the confidence value.';

-- GraphQL resolves a document to its annotation ids, then walks this index in
-- least-confidence order. The partial predicate keeps legacy/unscored rows out
-- of the ranked index while leaving them valid and nullable.
CREATE INDEX IF NOT EXISTS idx_entity_mention_annotation_confidence
    ON entity_mention (annotation_id, confidence, id)
    WHERE confidence IS NOT NULL;

INSERT INTO schema_migrations (filename) VALUES ('138_mention_confidence.sql')
    ON CONFLICT (filename) DO NOTHING;
