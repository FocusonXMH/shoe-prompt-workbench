BEGIN;
CREATE TABLE IF NOT EXISTS shoe_workbench.password_requests (
    id text PRIMARY KEY, identifier text NOT NULL, note text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','closed')),
    created_at timestamptz NOT NULL DEFAULT now(), handled_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS shoe_password_requests_pending ON shoe_workbench.password_requests(lower(identifier)) WHERE status='pending';
GRANT SELECT,INSERT,UPDATE,DELETE ON shoe_workbench.password_requests TO shoe_workbench_staging_app;
COMMIT;
