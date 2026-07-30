-- doc-ray: caption retraction audit columns (BL-201).
--
-- The Tier-3 caption flow inverts: proposals are approved on write and a reader
-- RETRACTS one they can see is wrong, instead of 5,851 proposals waiting on a
-- pre-publication decision that was never going to be made (21 of 6,122 ever
-- committed).
--
-- The verification requirement does not go away — it moves. A retraction is now
-- the human verification event, so it must be as auditable as an approval was:
-- who retracted, when, and why. Without these columns a retraction would be
-- indistinguishable from the model never having produced the caption, and the
-- PALS audit trail would be strictly weaker than the flow it replaced.
--
-- Additive: three nullable columns and one partial index. No existing column,
-- policy or signature changes, so a rolling deploy reads old rows unchanged.

ALTER TABLE visual_caption
    ADD COLUMN IF NOT EXISTS retracted_at timestamptz,
    ADD COLUMN IF NOT EXISTS retracted_by uuid REFERENCES principals (id),
    ADD COLUMN IF NOT EXISTS retraction_reason text;

COMMENT ON COLUMN visual_caption.retracted_at IS
    'When a human retracted this caption from the reader. NULL = never retracted.';
COMMENT ON COLUMN visual_caption.retracted_by IS
    'Principal who retracted it — the human verification event under PALS.';
COMMENT ON COLUMN visual_caption.retraction_reason IS
    'Operator-supplied reason, kept so a pattern of bad captions is visible.';

-- Retracted captions are the exception set an operator reviews for patterns, so
-- the index covers only them rather than the whole table.
CREATE INDEX IF NOT EXISTS visual_caption_retracted_idx
    ON visual_caption (retracted_at DESC)
    WHERE retracted_at IS NOT NULL;

INSERT INTO schema_migrations (filename)
VALUES ('150_caption_retraction.sql')
ON CONFLICT (filename) DO NOTHING;
