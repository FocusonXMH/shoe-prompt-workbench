-- Shoe prompt workbench: isolated application schema. Not applied automatically.
-- CPO identity tables remain owned and managed by the existing CPO application.
BEGIN;
CREATE SCHEMA IF NOT EXISTS shoe_workbench;

CREATE TABLE IF NOT EXISTS shoe_workbench.selling_points (
    id text PRIMARY KEY,
    title text NOT NULL,
    description text NOT NULL DEFAULT '',
    position integer NOT NULL DEFAULT 0,
    archived_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shoe_workbench.templates (
    id text PRIMARY KEY,
    selling_point_id text REFERENCES shoe_workbench.selling_points(id),
    title text NOT NULL,
    media_type text NOT NULL CHECK (media_type IN ('image', 'video')),
    position integer NOT NULL DEFAULT 0,
    draft jsonb,
    published_version integer,
    archived_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shoe_workbench.template_versions (
    template_id text NOT NULL REFERENCES shoe_workbench.templates(id),
    version integer NOT NULL CHECK (version > 0),
    content jsonb NOT NULL,
    published_by text NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (template_id, version)
);

CREATE TABLE IF NOT EXISTS shoe_workbench.user_preferences (
    user_id text PRIMARY KEY,
    preferences jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shoe_workbench.feedback (
    id text PRIMARY KEY,
    author_id text NOT NULL,
    template_id text REFERENCES shoe_workbench.templates(id),
    category text NOT NULL,
    content text NOT NULL,
    anonymous_to_users boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','awaiting_confirmation','resolved','withdrawn')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

CREATE TABLE IF NOT EXISTS shoe_workbench.feedback_messages (
    id text PRIMARY KEY,
    feedback_id text NOT NULL REFERENCES shoe_workbench.feedback(id),
    author_id text NOT NULL,
    content text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shoe_workbench.attachments (
    id text PRIMARY KEY,
    owner_id text NOT NULL,
    feedback_message_id text REFERENCES shoe_workbench.feedback_messages(id),
    storage_key text NOT NULL UNIQUE,
    content_type text NOT NULL,
    byte_size bigint NOT NULL CHECK (byte_size >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shoe_workbench.announcements (
    id text PRIMARY KEY,
    title text NOT NULL,
    content text NOT NULL,
    audience_user_id text,
    status text NOT NULL CHECK (status IN ('draft','published','withdrawn')),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shoe_workbench.announcement_reads (
    announcement_id text NOT NULL REFERENCES shoe_workbench.announcements(id),
    user_id text NOT NULL,
    read_method text NOT NULL CHECK (read_method IN ('opened','marked_read')),
    read_at timestamptz NOT NULL DEFAULT now(),
    hidden_at timestamptz,
    PRIMARY KEY (announcement_id, user_id)
);
COMMIT;
