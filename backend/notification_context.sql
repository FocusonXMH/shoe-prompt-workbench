ALTER TABLE shoe_workbench.announcements ADD COLUMN IF NOT EXISTS feedback_id text;
ALTER TABLE shoe_workbench.announcements ADD COLUMN IF NOT EXISTS feedback_message_id text;
CREATE INDEX IF NOT EXISTS announcements_feedback_idx ON shoe_workbench.announcements(feedback_id);
UPDATE shoe_workbench.announcements SET feedback_id=(regexp_match(content,'反馈 ([0-9a-f-]{36})'))[1]
 WHERE feedback_id IS NULL AND audience_user_id IS NOT NULL AND id LIKE 'feedback-notice-%';
UPDATE shoe_workbench.announcements a SET deleted_at=now()
 WHERE a.deleted_at IS NULL AND a.feedback_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM shoe_workbench.feedback f WHERE f.id=a.feedback_id AND f.deleted_at IS NULL);
