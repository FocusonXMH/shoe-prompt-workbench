BEGIN;
CREATE TABLE IF NOT EXISTS shoe_workbench.settings (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
