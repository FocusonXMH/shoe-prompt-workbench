BEGIN;
CREATE TABLE IF NOT EXISTS shoe_workbench.local_accounts (
  id text PRIMARY KEY, username text NOT NULL UNIQUE, display_name text NOT NULL,
  role_code text NOT NULL CHECK (role_code IN ('operator','super_admin')),
  category text NOT NULL DEFAULT '', password_hash text NOT NULL,
  active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS shoe_workbench.local_sessions (
  token_hash text PRIMARY KEY, user_id text NOT NULL REFERENCES shoe_workbench.local_accounts(id),
  expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS shoe_workbench.login_attempts (
  key text PRIMARY KEY, attempts integer NOT NULL, reset_at timestamptz NOT NULL
);
COMMIT;
