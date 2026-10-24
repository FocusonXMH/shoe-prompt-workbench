BEGIN;
ALTER TABLE shoe_workbench.template_versions ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
ALTER TABLE shoe_workbench.template_versions ADD COLUMN IF NOT EXISTS deleted_by text;
COMMIT;
