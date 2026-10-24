"""Manage the existing shared identity store; business data stays in shoe DB."""
import base64
import hashlib
import secrets

import asyncpg
from fastapi import HTTPException


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000)
    enc = lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
    return f'pbkdf2_sha256$600000${enc(salt)}${enc(digest)}'


async def directory(pool):
    async with pool.acquire() as conn:
        return [dict(r, user_id=str(r['user_id'])) for r in await conn.fetch('''
            SELECT user_id,username,display_name,role_code,status,operator_code
            FROM iam.users ORDER BY user_id''')]


async def save(pool, account_id, username, name, role, active, password=None):
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                # Serialize account-name and operator-code allocation across workbench requests.
                await conn.execute('SELECT pg_advisory_xact_lock(73410242)')
                row = await conn.fetchrow('SELECT user_id,role_code,operator_code FROM iam.users WHERE user_id=$1 FOR UPDATE', int(account_id)) if account_id else None
                if account_id and not row:
                    raise HTTPException(404, 'account not found')
                if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM iam.users WHERE lower(username)=lower($1) AND user_id<>$2)', username, int(account_id or 0)):
                    raise HTTPException(409, 'username already exists')
                if role not in ('operator','ai','management','super_admin','workbench_user'):
                    raise HTTPException(422, 'invalid role')
                code = row['operator_code'] if row and row['role_code'] == 'operator' else None
                if role == 'operator' and not code:
                    # Preserve all existing operators. New people receive a free code, not a reused one.
                    codes = {r['operator_code'] for r in await conn.fetch('SELECT operator_code FROM iam.users WHERE operator_code IS NOT NULL')}
                    number = 1
                    while f'SHOE{number:04d}' in codes:
                        number += 1
                    code = f'SHOE{number:04d}'
                if role != 'operator':
                    code = None
                if row:
                    result = await conn.fetchrow('''UPDATE iam.users SET username=$2,display_name=$3,role_code=$4,
                        operator_code=$5,status=$6,updated_at=now() WHERE user_id=$1
                        RETURNING user_id,username,display_name,role_code,status''', int(account_id),username,name,role,code,'active' if active else 'disabled')
                    if not active or role != row['role_code']:
                        await conn.execute('UPDATE iam.user_sessions SET revoked_at=now() WHERE user_id=$1 AND revoked_at IS NULL', int(account_id))
                else:
                    result = await conn.fetchrow('''INSERT INTO iam.users(username,display_name,role_code,operator_code,password_hash,status)
                        VALUES($1,$2,$3,$4,$5,'active') RETURNING user_id,username,display_name,role_code,status''',username,name,role,code,password_hash(password))
                return dict(result, user_id=str(result['user_id']))
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, 'username already exists')


async def reset(pool, account_id):
    password = f'{secrets.randbelow(1000000):06d}'
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow('''UPDATE iam.users SET password_hash=$2,updated_at=now()
                WHERE user_id=$1 RETURNING username,display_name''',int(account_id),password_hash(password))
            if not row:
                raise HTTPException(404, 'account not found')
            await conn.execute('UPDATE iam.user_sessions SET revoked_at=now() WHERE user_id=$1 AND revoked_at IS NULL',int(account_id))
    return dict(row, initial_password=password)


async def delete(pool, account_id):
    async with pool.acquire() as conn:
        try:
            result = await conn.execute('DELETE FROM iam.users WHERE user_id=$1',int(account_id))
        except asyncpg.ForeignKeyViolationError:
            raise HTTPException(409, 'account has linked records; disable instead')
    if result == 'DELETE 0':
        raise HTTPException(404, 'account not found')
