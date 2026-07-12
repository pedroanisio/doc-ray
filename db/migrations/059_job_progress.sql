-- doc-ray: first-class async job progress (BL-123).

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS progress_current int,
    ADD COLUMN IF NOT EXISTS progress_total int,
    ADD COLUMN IF NOT EXISTS progress_stage text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'jobs_progress_current_nonnegative'
          AND conrelid = 'jobs'::regclass
    ) THEN
        ALTER TABLE jobs
            ADD CONSTRAINT jobs_progress_current_nonnegative
            CHECK (progress_current IS NULL OR progress_current >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'jobs_progress_total_nonnegative'
          AND conrelid = 'jobs'::regclass
    ) THEN
        ALTER TABLE jobs
            ADD CONSTRAINT jobs_progress_total_nonnegative
            CHECK (progress_total IS NULL OR progress_total >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'jobs_progress_current_lte_total'
          AND conrelid = 'jobs'::regclass
    ) THEN
        ALTER TABLE jobs
            ADD CONSTRAINT jobs_progress_current_lte_total
            CHECK (
                progress_current IS NULL
                OR progress_total IS NULL
                OR progress_current <= progress_total
            );
    END IF;
END $$;

INSERT INTO schema_migrations (filename)
VALUES ('059_job_progress.sql')
ON CONFLICT (filename) DO NOTHING;
