-- Independent workbench metrics; no prompt input or password contents.
BEGIN;
CREATE TABLE IF NOT EXISTS shoe_workbench.events (
    id text PRIMARY KEY,
    user_id text NOT NULL,
    event_type text NOT NULL CHECK (event_type IN ('template_open','prompt_copy','version_select')),
    template_id text NOT NULL REFERENCES shoe_workbench.templates(id),
    template_version integer,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS shoe_events_template_time ON shoe_workbench.events(template_id,created_at);
COMMIT;
