-- doc-ray: entity-resolution adjudication — review queue + merge audit (D-021).
-- Low-confidence / abstained / conflicting resolutions land in resolution_review for a
-- human (curator/admin per 018_rbac); duplicate canonicals are merged and logged.

CREATE TABLE IF NOT EXISTS resolution_review (
    id                    ulid PRIMARY KEY,
    mention_id            ulid NOT NULL REFERENCES entity_mention(id) ON DELETE CASCADE,
    candidates            jsonb NOT NULL DEFAULT '[]'::jsonb,   -- the Stage-2 candidate set shown
    proposed_canonical_id bigint REFERENCES canonical(id) ON DELETE SET NULL,
    confidence            real,
    status                text NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'approved', 'rejected', 'merged')),
    reason                text,
    reviewer              uuid REFERENCES principals(id) ON DELETE SET NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    decided_at            timestamptz,
    CHECK (decided_at IS NULL OR status <> 'pending')
);
CREATE INDEX IF NOT EXISTS idx_resolution_review_status ON resolution_review (status, id);

-- Merge audit: from_id's row is deleted (edges repointed to into_id), so from_id has no FK.
CREATE TABLE IF NOT EXISTS canonical_merge (
    from_id   bigint NOT NULL,
    into_id   bigint NOT NULL REFERENCES canonical(id) ON DELETE CASCADE,
    actor     uuid REFERENCES principals(id) ON DELETE SET NULL,
    reason    text,
    merged_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (from_id, into_id, merged_at)
);

INSERT INTO schema_migrations (filename) VALUES ('023_resolution.sql')
    ON CONFLICT (filename) DO NOTHING;
