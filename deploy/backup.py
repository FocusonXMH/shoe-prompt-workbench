"""Private local disaster-recovery snapshots; not a substitute for off-host backup."""
import datetime
import os
from pathlib import Path
import subprocess
import tarfile
from urllib.parse import urlsplit, unquote

root = Path('/home/ubuntu/shoe-workbench-staging')
target = root / 'backups'
target.mkdir(mode=0o700, exist_ok=True)
os.chmod(target, 0o700)
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
db_file = target / ('workbench-' + stamp + '.pgdump')
image_file = target / ('uploads-' + stamp + '.tar.gz')
env = os.environ.copy()
connection = urlsplit(env.pop('SHOE_DATABASE_URL'))
env.update(PGHOST=connection.hostname or 'localhost', PGPORT=str(connection.port or 5432),
           PGUSER=unquote(connection.username or ''), PGPASSWORD=unquote(connection.password or ''),
           PGDATABASE=unquote(connection.path.lstrip('/')))
os.umask(0o077)
with db_file.open('xb') as stream:
    subprocess.run(['pg_dump', '--format=custom', '--no-owner', '--no-acl'], env=env, stdout=stream, check=True)
with tarfile.open(image_file, 'x:gz') as archive:
    archive.add(root / 'uploads', arcname='uploads')
print('Private database and upload snapshots created:', stamp)
