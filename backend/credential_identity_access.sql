DO $$ DECLARE role_name text; BEGIN
 FOR role_name IN SELECT DISTINCT grantee FROM information_schema.column_privileges
 WHERE table_schema='iam' AND table_name='users' AND column_name='username' AND privilege_type='SELECT'
 AND grantee LIKE 'shoe%'
 LOOP EXECUTE format('GRANT SELECT(password_hash) ON iam.users TO %I',role_name); END LOOP;
END $$;
