BEGIN;
ALTER TABLE shoe_workbench.feedback_messages ADD COLUMN IF NOT EXISTS reply_to text REFERENCES shoe_workbench.feedback_messages(id);
COMMIT;
