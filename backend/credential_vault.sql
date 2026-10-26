CREATE TABLE IF NOT EXISTS shoe_workbench.credential_vault (
 account_id text PRIMARY KEY,ciphertext text NOT NULL,identity_hash text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now()
);
DO $$ DECLARE role_name text; BEGIN
 FOR role_name IN SELECT grantee FROM information_schema.role_table_grants
 WHERE table_schema='shoe_workbench' AND table_name='account_audit' AND privilege_type='INSERT'
 LOOP EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON shoe_workbench.credential_vault TO %I',role_name); END LOOP;
END $$;
