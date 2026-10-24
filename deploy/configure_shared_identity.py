"""Provision narrowly scoped identity access, leaving CPO code and business tables alone."""
import os
import secrets
import subprocess
from pathlib import Path

root = Path('/home/ubuntu/shoe-workbench-staging')
env = root / 'database.env'
text = env.read_text()
if 'SHOE_IDENTITY_DATABASE_URL=' in text:
    print('Identity connection already configured')
    raise SystemExit(0)
password = secrets.token_urlsafe(40)
sql = f'''BEGIN;
CREATE ROLE shoe_identity_manager LOGIN PASSWORD '{password}';
GRANT CONNECT ON DATABASE amazon_ads TO shoe_identity_manager;
GRANT USAGE ON SCHEMA iam TO shoe_identity_manager;
GRANT SELECT(user_id,username,display_name,role_code,status,operator_code) ON iam.users TO shoe_identity_manager;
GRANT INSERT(username,display_name,role_code,operator_code,password_hash,status),
 UPDATE(username,display_name,role_code,operator_code,password_hash,status,updated_at), DELETE ON iam.users TO shoe_identity_manager;
GRANT SELECT(user_id,revoked_at),UPDATE(revoked_at) ON iam.user_sessions TO shoe_identity_manager;
GRANT USAGE ON SEQUENCE iam.users_user_id_seq TO shoe_identity_manager;
INSERT INTO iam.roles(role_code,display_name,description) VALUES('workbench_user','普通成员','工作台普通成员，业务数据权限另外分配') ON CONFLICT DO NOTHING;
COMMIT;'''
result = subprocess.run(['sudo','-u','postgres','psql','-X','-v','ON_ERROR_STOP=1','-d','amazon_ads'],input=sql,text=True,capture_output=True)
if result.returncode:
    raise RuntimeError('Identity access setup failed; inspect schema and role state')
env.write_text(text.rstrip() + '\nSHOE_IDENTITY_DATABASE_URL=postgresql://shoe_identity_manager:' + password + '@127.0.0.1:5432/amazon_ads\n')
os.chmod(env,0o600)
print('Identity access configured: identity tables only')
