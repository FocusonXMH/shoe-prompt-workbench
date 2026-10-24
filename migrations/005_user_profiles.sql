BEGIN;
CREATE TABLE IF NOT EXISTS shoe_workbench.user_profiles (
    user_id text PRIMARY KEY,
    username text NOT NULL,
    display_name text NOT NULL,
    role_code text NOT NULL,
    last_seen timestamptz NOT NULL DEFAULT now()
);
COMMIT;
