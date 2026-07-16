-- doc-ray: Prism Tier-3 visual layer — grounded VLM captions + review queue.
--
-- The ONLY tier that consumes untrusted model output, so it carries the full
-- PALS treatment (proposal §3, Tier 3): a caption is committed to visual_caption
-- only when its specificity-weighted grounding_score clears the auto-commit bar;
-- below-threshold output lands in visual_review and is NEVER exposed as trusted.
--
-- visual_review's RLS is NET-NEW (not inherited): resolution_review has no
-- document_id and no RLS registration, so this table carries its own denormalized
-- document_id + docray_attach_rls + a cross-tenant isolation test.

ALTER TYPE provenance_kind ADD VALUE IF NOT EXISTS 'vision_captioned';

CREATE TABLE IF NOT EXISTS visual_caption (
    id                ulid PRIMARY KEY,
    document_image_id ulid NOT NULL REFERENCES document_image(id) ON DELETE CASCADE,
    document_id       uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,  -- denormalized for RLS
    caption           text NOT NULL,
    analysis          jsonb,
    model             text NOT NULL,
    model_version     text,
    grounding_score   real NOT NULL,
    grounded_against  jsonb NOT NULL DEFAULT '{}'::jsonb,   -- evidence + matched terms (auditable)
    status            text NOT NULL DEFAULT 'review'
                        CHECK (status IN ('committed', 'review', 'rejected')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_image_id, model)
);

CREATE INDEX IF NOT EXISTS idx_visual_caption_doc ON visual_caption (document_id);
CREATE INDEX IF NOT EXISTS idx_visual_caption_committed
    ON visual_caption (document_id) WHERE status = 'committed';

SELECT docray_attach_rls('visual_caption'::regclass, 'document_id IN (%DOCS%)');
GRANT SELECT, INSERT, UPDATE, DELETE ON visual_caption TO docray_client;


CREATE TABLE IF NOT EXISTS visual_review (
    id                ulid PRIMARY KEY,
    document_image_id ulid NOT NULL REFERENCES document_image(id) ON DELETE CASCADE,
    document_id       uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,  -- net-new RLS anchor
    proposed          jsonb NOT NULL,                       -- the ungated caption + analysis
    grounding_score   real NOT NULL,
    grounding_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    status            text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewer          uuid REFERENCES principals(id),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_visual_review_pending
    ON visual_review (document_id) WHERE status = 'pending';

DROP TRIGGER IF EXISTS trg_visual_review_updated_at ON visual_review;
CREATE TRIGGER trg_visual_review_updated_at
    BEFORE UPDATE ON visual_review
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

SELECT docray_attach_rls('visual_review'::regclass, 'document_id IN (%DOCS%)');
GRANT SELECT, INSERT, UPDATE, DELETE ON visual_review TO docray_client;

INSERT INTO schema_migrations (filename) VALUES ('093_visual_tier3.sql')
    ON CONFLICT (filename) DO NOTHING;
