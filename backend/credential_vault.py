"""Administrator-only recoverable credentials, separate from identity hashes."""
import os
import hashlib
import hmac
import base64
from pathlib import Path
from cryptography.fernet import Fernet

def cipher():
    path=Path(os.environ.get('SHOE_CREDENTIAL_KEY_FILE', '/home/ubuntu/shoe-workbench-staging/credential.key'))
    if not path.exists():
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as out: out.write(Fernet.generate_key())
        except FileExistsError: pass
    return Fernet(path.read_bytes())

async def initialize(pool):
    cipher()

async def identity(pool, shared_pool, account_id):
    local=account_id.startswith('shoe-user-')
    async with (pool if local else shared_pool).acquire() as conn:
        return await conn.fetchrow('SELECT username,display_name,password_hash FROM shoe_workbench.local_accounts WHERE id=$1' if local else 'SELECT username,display_name,password_hash FROM iam.users WHERE user_id=$1',account_id if local else int(account_id))

async def store(pool,shared_pool,account_id,password):
    row=await identity(pool,shared_pool,str(account_id))
    async with pool.acquire() as conn:
        await conn.execute('''INSERT INTO shoe_workbench.credential_vault(account_id,ciphertext,identity_hash)
          VALUES($1,$2,$3) ON CONFLICT(account_id) DO UPDATE SET ciphertext=$2,identity_hash=$3,updated_at=now()''',str(account_id),cipher().encrypt(password.encode()).decode(),row['password_hash'])

async def reveal(pool,shared_pool,account_id):
    row=await identity(pool,shared_pool,account_id)
    async with pool.acquire() as conn:
        secret=await conn.fetchrow('SELECT ciphertext,identity_hash FROM shoe_workbench.credential_vault WHERE account_id=$1',account_id)
    if not row or not secret or not hmac.compare_digest(row['password_hash'],secret['identity_hash']): return None
    return dict(username=row['username'],display_name=row['display_name'],password=cipher().decrypt(secret['ciphertext'].encode()).decode())

def matches(password,encoded):
    try:
        algorithm,iterations,salt,digest=encoded.split('$')
        if algorithm!='pbkdf2_sha256': return False
        decode=lambda s:base64.urlsafe_b64decode(s+'='*(-len(s)%4))
        return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),decode(salt),int(iterations)),decode(digest))
    except (ValueError,TypeError): return False
