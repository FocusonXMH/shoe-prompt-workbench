BEGIN;
ALTER TABLE shoe_workbench.feedback ADD COLUMN IF NOT EXISTS announcement_id text REFERENCES shoe_workbench.announcements(id) ON DELETE SET NULL;
ALTER TABLE shoe_workbench.announcements ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
CREATE TABLE IF NOT EXISTS shoe_workbench.field_usage (
 user_id text NOT NULL, template_id text NOT NULL REFERENCES shoe_workbench.templates(id),
 template_version integer NOT NULL, field_key text NOT NULL, field_label text NOT NULL,
 value text NOT NULL CHECK(length(value)<=60), source text NOT NULL CHECK(source IN ('preset','custom')),
 use_count bigint NOT NULL DEFAULT 1, last_used timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(user_id,template_id,template_version,field_key,value)
);
CREATE INDEX IF NOT EXISTS shoe_field_usage_template ON shoe_workbench.field_usage(template_id,template_version);
GRANT SELECT,INSERT,UPDATE,DELETE ON shoe_workbench.field_usage TO shoe_workbench_staging_app;
COMMIT;
