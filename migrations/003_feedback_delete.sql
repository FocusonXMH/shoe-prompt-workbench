BEGIN;
ALTER TABLE shoe_workbench.feedback ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
COMMIT;
