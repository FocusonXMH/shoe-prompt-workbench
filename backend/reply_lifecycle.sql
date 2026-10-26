ALTER TABLE shoe_workbench.feedback_messages ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active';
ALTER TABLE shoe_workbench.feedback_messages ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE shoe_workbench.feedback_messages ADD COLUMN IF NOT EXISTS edited_at timestamptz;
