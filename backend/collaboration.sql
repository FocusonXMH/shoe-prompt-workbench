ALTER TABLE shoe_workbench.feedback_messages ADD COLUMN IF NOT EXISTS anonymous_to_users boolean NOT NULL DEFAULT false;
ALTER TABLE shoe_workbench.local_accounts DROP CONSTRAINT IF EXISTS local_accounts_role_code_check;
ALTER TABLE shoe_workbench.local_accounts ADD CONSTRAINT local_accounts_role_code_check CHECK(role_code IN ('operator','management','super_admin'));
UPDATE shoe_workbench.feedback_messages m SET anonymous_to_users=true FROM shoe_workbench.feedback f
 WHERE m.feedback_id=f.id AND m.author_id=f.author_id AND f.anonymous_to_users AND NOT m.anonymous_to_users;
CREATE TABLE IF NOT EXISTS shoe_workbench.remembered_accounts (
 device_hash text NOT NULL,account_id text NOT NULL,username text NOT NULL,display_name text NOT NULL,
 token_ciphertext text NOT NULL,expires_at timestamptz NOT NULL DEFAULT now()+interval '30 days',
 updated_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(device_hash,account_id));
DO $$ DECLARE r text; BEGIN
 FOR r IN SELECT grantee FROM information_schema.role_table_grants WHERE table_schema='shoe_workbench' AND table_name='account_audit' AND privilege_type='INSERT'
 LOOP EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON shoe_workbench.remembered_accounts TO %I',r); END LOOP;
END $$;
