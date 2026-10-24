"""Provision a least-privilege DB login for the isolated staging database.

Run as the staging owner on the authorized server. No credentials are printed.
Existing roles and credential files are never replaced.
"""
import os
import secrets
import subprocess
from pathlib import Path

root = Path('/home/ubuntu/shoe-workbench-staging')
credential = root / 'database.env'
if credential.exists():
    raise SystemExit('Existing staging credentials found; refusing to overwrite')
password = secrets.token_hex(32)
sql = f"""\\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE shoe_workbench_staging_app LOGIN PASSWORD '{password}';
GRANT CONNECT ON DATABASE shoe_workbench_staging TO shoe_workbench_staging_app;
GRANT USAGE ON SCHEMA shoe_workbench TO shoe_workbench_staging_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA shoe_workbench TO shoe_workbench_staging_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA shoe_workbench TO shoe_workbench_staging_app;
COMMIT;
"""
proc = subprocess.run(['sudo', '-n', '-u', 'postgres', 'psql', '-X', '-q', '-d', 'shoe_workbench_staging'],
                      input=sql, text=True, capture_output=True)
if proc.returncode:
    raise SystemExit('Database role provisioning failed; no credential file created')
descriptor = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
    output.write('SHOE_DATABASE_URL=postgresql://shoe_workbench_staging_app:' + password + '@127.0.0.1:5432/shoe_workbench_staging\n')
print('isolated_database_role_created')
