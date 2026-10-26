-- Keep the referenced publication identity and label; content remains in template_versions.
ALTER TABLE shoe_workbench.feedback_messages ADD COLUMN IF NOT EXISTS template_reference jsonb;
