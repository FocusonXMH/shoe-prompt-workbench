"""Shoe prompt workbench API with isolated business data and shared identity."""
import json
import os
import re
import uuid
import hashlib
import hmac
import secrets
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import asyncpg
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from identity import CpoIdentityProvider, Principal
from policy import WorkbenchUser, can_transition_feedback
import shared_accounts
import credential_vault
import collaboration
import help_center
import media_support


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No fallback to the CPO business database. Configuration must be explicit.
    dsn = os.environ.get('SHOE_DATABASE_URL')
    if not dsn:
        raise RuntimeError('SHOE_DATABASE_URL must point to the isolated workbench database')
    app.state.db = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    identity_dsn = os.environ.get('SHOE_IDENTITY_DATABASE_URL')
    app.state.accounts = await asyncpg.create_pool(identity_dsn, min_size=1, max_size=3) if identity_dsn else None
    await credential_vault.initialize(app.state.db)
    app.state.identity_client = httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=5)
    app.state.identity = CpoIdentityProvider(app.state.identity_client)
    try:
        yield
    finally:
        await app.state.identity_client.aclose()
        await app.state.db.close()
        if app.state.accounts:
            await app.state.accounts.close()


app = FastAPI(title='Shoe prompt workbench API', lifespan=lifespan, docs_url=None, redoc_url=None)


async def current_user(request: Request, authorization: str = Header(default='')):
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not token:
        token = request.cookies.get('shoe_embed_session' if getattr(request.state,'embedded',False) else 'shoe_session', '')
    if not token:
        raise HTTPException(401, 'authentication required')
    try:
        if token.startswith('shoe-local.'):
            digest = hashlib.sha256(token.encode()).hexdigest()
            async with app.state.db.acquire() as conn:
                row = await conn.fetchrow('''SELECT a.id,a.username,a.display_name,a.role_code
                  FROM shoe_workbench.local_sessions s JOIN shoe_workbench.local_accounts a ON a.id=s.user_id
                  WHERE s.token_hash=$1 AND s.expires_at>now() AND a.active''', digest)
            principal = Principal(row['id'], row['username'], row['display_name'], row['role_code']) if row else None
        else:
            principal = await app.state.identity.resolve(token)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, 'identity service unavailable')
    if principal is None:
        raise HTTPException(401, 'invalid session')
    # Until an independent role mapping is implemented, only the existing super
    # administrator is allowed to administer this separate application.
    if app.state.accounts and not principal.user_id.startswith('shoe-user-'):
        async with app.state.accounts.acquire() as conn:
            actual=await conn.fetchrow('SELECT role_code,status FROM iam.users WHERE user_id=$1',int(principal.user_id))
        if not actual or actual['status']!='active': raise HTTPException(401,'account disabled')
        principal=Principal(principal.user_id,principal.username,principal.display_name,actual['role_code'])
    return WorkbenchUser(principal.user_id, principal.role in ('super_admin','management')), principal


class FeedbackCreate(BaseModel):
    submission_id: uuid.UUID | None = None
    announcement_id: str | None = Field(default=None,max_length=100)
    category: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=5000)
    template_id: str | None = Field(default=None, max_length=100)
    template_version: int | None = Field(default=None, ge=1)
    anonymous_to_users: bool = False


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    remember: bool = True


class LocalAccountInput(BaseModel):
    username: str = Field(min_length=7, max_length=64, pattern=r'^shoe_[a-z0-9_]+$')
    display_name: str = Field(min_length=1, max_length=100)
    category: str = Field(default='', max_length=200)
    role: str = 'operator'


class LocalAccountUpdate(BaseModel):
    username: str | None = Field(default=None,min_length=3,max_length=64,pattern=r'^[a-z][a-z0-9_]+$')
    display_name: str = Field(min_length=1, max_length=100)
    category: str = Field(default='', max_length=200)
    active: bool
    role: str


class AccountInput(BaseModel):
    username: str = Field(min_length=3,max_length=64,pattern=r'^[a-z][a-z0-9_]+$')
    display_name: str = Field(min_length=1,max_length=100)
    category: str = Field(default='',max_length=200)
    role: str = 'workbench_user'
    active: bool = True


def hash_local_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310000)
    return salt.hex() + ':' + digest.hex()


def verify_local_password(password: str, encoded: str) -> bool:
    try:
        salt, _ = encoded.split(':', 1)
        return hmac.compare_digest(hash_local_password(password, bytes.fromhex(salt)), encoded)
    except (ValueError, TypeError):
        return False


class FeedbackTransition(BaseModel):
    status: str


class PasswordRequestInput(BaseModel):
    identifier: str = Field(min_length=1,max_length=128)
    note: str = Field(default='',max_length=1000)


class TemplateDraft(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    media_type: str
    content: dict
    selling_point_id: str | None = None


class VersionName(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class FeedbackMessage(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    reply_to: str | None = Field(default=None, max_length=100)
    anonymous_to_users: bool = False
    template_id: str | None = Field(default=None, max_length=100)


class FeedbackEdit(BaseModel):
    content: str = Field(min_length=1, max_length=5000)


class AnnouncementInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20000)
    audience_user_id: str | None = Field(default=None, max_length=200)
    status: str = 'draft'
    images: list[str] | None = Field(default=None, max_length=50)


class ReadInput(BaseModel):
    method: str = 'opened'


class SellingPointInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=2000)
    position: int = 0


class FieldUsageInput(BaseModel):
    key: str = Field(min_length=1,max_length=100)
    value: str = Field(min_length=1,max_length=60)


class EventInput(BaseModel):
    event_type: str
    template_id: str = Field(min_length=1, max_length=100)
    template_version: int | None = Field(default=None, ge=1)
    field_values: list[FieldUsageInput] = Field(default_factory=list,max_length=100)


class HelpInput(BaseModel):
    text: str = Field(default='', max_length=20000)
    images: list[str] = Field(default_factory=list, max_length=50)


class OrderInput(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)


class FeedbackType(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    example: str = Field(default='', max_length=1000)
    active: bool = True


def json_value(value):
    return json.loads(value) if isinstance(value, str) else value


def validate_template_content(content, publish=False):
    """Validate explicit bracket rules without guessing their meaning."""
    if not isinstance(content, dict):
        raise HTTPException(422, 'template content must be an object')
    body=content.get('body','')
    if not isinstance(body,str):
        raise HTTPException(422,'template body must be text')
    if publish and not body.strip():
        raise HTTPException(422,'发布前请填写提示词正文')
    if not publish:
        return
    rules = content.get('bracketFields')
    if rules is None:
        return
    if not isinstance(rules, list) or len(rules) > 1000:
        raise HTTPException(422, 'invalid bracket rules')
    body = content.get('body', '')
    if not isinstance(body, str):
        raise HTTPException(422, 'template body must be text')
    count = len(re.findall(r'【[^【】]*】', body))
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise HTTPException(422, 'invalid bracket rule')
        index = rule.get('index')
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= count or index in seen:
            raise HTTPException(422, 'invalid or duplicate bracket index')
        if rule.get('mode', 'replace') not in ('fixed', 'replace', 'prefix', 'optional', 'block'):
            raise HTTPException(422, 'invalid bracket rule mode')
        seen.add(index)
    if publish and len(seen) != count:
        raise HTTPException(422, 'all bracket positions require explicit confirmation')


def require_admin(user):
    if not user[0].is_admin:
        raise HTTPException(403, 'administrator permission required')


@app.middleware('http')
async def require_same_origin(request: Request, call_next):
    if request.scope['path'].startswith('/embedded/'):
        request.state.embedded=True
        request.scope['path']=request.scope['path'][len('/embedded'):]
        request.scope['raw_path']=request.scope['path'].encode()
        if request.scope['path'].startswith('/auth/accounts') or request.scope['path']=='/auth/login':
            return JSONResponse({'detail':'请在 AdSight 控制台切换登录账号'},status_code=409)
    # Browser cookie authentication is accepted only for same-origin writes.
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('origin'):
        origin = urlsplit(request.headers['origin'])
        host = request.headers.get('host', '').lower()
        if origin.scheme not in ('http', 'https') or origin.netloc.lower() != host:
            return JSONResponse({'detail': 'cross-origin request rejected'}, status_code=403)
    response=await call_next(request)
    if request.scope['path'].startswith('/shoe'):
        response.headers['Content-Security-Policy']="frame-ancestors 'self'"
    return response


@app.post('/auth/from-cpo')
async def adopt_cpo_session(request:Request,authorization:str=Header(default='')):
    scheme,_,token=authorization.partition(' ')
    if scheme.lower()!='bearer' or not token or token.startswith('shoe-local.'):
        raise HTTPException(401,'verified CPO session required')
    user=await current_user(request,authorization)
    response=JSONResponse({'id':user[0].user_id,'connected':True},headers={'Cache-Control':'no-store'})
    response.set_cookie('shoe_embed_session',token,secure=True,httponly=True,samesite='strict',path='/shoe-embed-api/')
    return response


@app.post('/auth/login')
async def login(payload: LoginInput, request: Request):
    async with app.state.db.acquire() as conn:
        is_local = await conn.fetchval('SELECT EXISTS(SELECT 1 FROM shoe_workbench.local_accounts WHERE lower(username)=lower($1))',payload.username.strip())
    if is_local:
        username = payload.username.lower()
        async with app.state.db.acquire() as conn:
            async with conn.transaction():
                attempts = await conn.fetchrow('''INSERT INTO shoe_workbench.login_attempts(key,attempts,reset_at)
                  VALUES($1,1,now()+interval '15 minutes') ON CONFLICT(key) DO UPDATE SET
                  attempts=CASE WHEN shoe_workbench.login_attempts.reset_at<now() THEN 1 ELSE shoe_workbench.login_attempts.attempts+1 END,
                  reset_at=CASE WHEN shoe_workbench.login_attempts.reset_at<now() THEN now()+interval '15 minutes' ELSE shoe_workbench.login_attempts.reset_at END
                  RETURNING attempts''', username)
                row = await conn.fetchrow('SELECT id,password_hash,active FROM shoe_workbench.local_accounts WHERE username=$1', username)
        if attempts['attempts'] > 8:
            raise HTTPException(429, 'too many login attempts')
        valid = await asyncio.to_thread(verify_local_password, payload.password, row['password_hash']) if row and row['active'] else False
        if not valid:
            raise HTTPException(401, 'invalid credentials')
        token = 'shoe-local.' + secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        async with app.state.db.acquire() as conn:
            await conn.execute("INSERT INTO shoe_workbench.local_sessions(token_hash,user_id,expires_at) VALUES($1,$2,now()+($3 * interval '1 second'))",digest,row['id'],604800 if payload.remember else 43200)
            await conn.execute('DELETE FROM shoe_workbench.login_attempts WHERE key=$1',username)
        result = {'accessToken': token}
    else:
        try:
            result = await app.state.identity.login(payload.username, payload.password)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, 'identity service unavailable')
        if result is None:
            raise HTTPException(401, 'invalid credentials')
    response = JSONResponse({'ok': True})
    response.set_cookie('shoe_session', result['accessToken'], httponly=True, secure=True,
                        samesite='strict', path='/', max_age=7 * 24 * 3600 if payload.remember else None)
    response.headers['Cache-Control'] = 'no-store'
    if payload.remember:
        await remember_account(request,response,result['accessToken'])
    return response


@app.post('/auth/logout')
async def logout(request: Request, user=Depends(current_user)):
    embedded=getattr(request.state,'embedded',False)
    token = request.cookies.get('shoe_embed_session' if embedded else 'shoe_session', '')
    if token.startswith('shoe-local.'):
        async with app.state.db.acquire() as conn:
            await conn.execute('DELETE FROM shoe_workbench.local_sessions WHERE token_hash=$1',hashlib.sha256(token.encode()).hexdigest())
    elif token:
        try:
            await app.state.identity.logout(token)
        except httpx.HTTPError:
            raise HTTPException(503, 'identity service unavailable')
    response = JSONResponse({'ok': True})
    response.delete_cookie('shoe_embed_session' if embedded else 'shoe_session', path='/shoe-embed-api/' if embedded else '/', secure=True, httponly=True, samesite='strict')
    return response


def device_hash(request):
    value=request.cookies.get('shoe_device','')
    return hashlib.sha256(value.encode()).hexdigest() if value else None


async def remember_account(request,response,new_token):
    device=request.cookies.get('shoe_device') or secrets.token_urlsafe(32)
    digest=hashlib.sha256(device.encode()).hexdigest()
    for token in [request.cookies.get('shoe_session',''),new_token]:
        if not token: continue
        try: _,person=await current_user(request,'Bearer '+token)
        except HTTPException: continue
        async with app.state.db.acquire() as conn:
            await conn.execute('''INSERT INTO shoe_workbench.remembered_accounts(device_hash,account_id,username,display_name,token_ciphertext)
              VALUES($1,$2,$3,$4,$5) ON CONFLICT(device_hash,account_id) DO UPDATE SET username=$3,display_name=$4,
              token_ciphertext=$5,expires_at=now()+interval '30 days',updated_at=now()''',digest,person.user_id,person.username,person.display_name,credential_vault.cipher().encrypt(token.encode()).decode())
            await conn.execute("DELETE FROM shoe_workbench.remembered_accounts WHERE expires_at<now()")
    response.set_cookie('shoe_device',device,httponly=True,secure=True,samesite='strict',path='/',max_age=30*86400)


@app.get('/auth/accounts')
async def remembered_accounts(request: Request,user=Depends(current_user)):
    async with app.state.db.acquire() as conn:
        rows=await conn.fetch('''SELECT account_id,username,display_name FROM shoe_workbench.remembered_accounts
          WHERE device_hash=$1 AND expires_at>now() ORDER BY updated_at DESC LIMIT 20''',device_hash(request))
    return JSONResponse([dict(r,current=r['account_id']==user[0].user_id) for r in rows],headers={'Cache-Control':'no-store'})


@app.post('/auth/accounts')
async def remember_current(request: Request,user=Depends(current_user)):
    response=JSONResponse({'ok':True})
    await remember_account(request,response,request.cookies.get('shoe_session',''))
    return response


@app.post('/auth/accounts/{account_id}/switch')
async def switch_remembered(account_id: str,request: Request,user=Depends(current_user)):
    async with app.state.db.acquire() as conn:
        row=await conn.fetchrow('SELECT token_ciphertext FROM shoe_workbench.remembered_accounts WHERE device_hash=$1 AND account_id=$2 AND expires_at>now()',device_hash(request),account_id)
    if not row: raise HTTPException(404,'账号记录不存在，请重新登录')
    token=credential_vault.cipher().decrypt(row['token_ciphertext'].encode()).decode()
    try: await current_user(request,'Bearer '+token)
    except HTTPException: raise HTTPException(401,'该账号登录已失效，请重新输入密码')
    response=JSONResponse({'ok':True},headers={'Cache-Control':'no-store'})
    response.set_cookie('shoe_session',token,httponly=True,secure=True,samesite='strict',path='/',max_age=7*86400)
    return response


@app.delete('/auth/accounts/{account_id}')
async def remove_remembered(account_id: str,request: Request,user=Depends(current_user)):
    if account_id==user[0].user_id: raise HTTPException(409,'不能移除当前使用的账号')
    async with app.state.db.acquire() as conn:
        await conn.execute('DELETE FROM shoe_workbench.remembered_accounts WHERE device_hash=$1 AND account_id=$2',device_hash(request),account_id)
    return {'removed':True}


@app.delete('/auth/accounts')
async def clear_remembered(request: Request,user=Depends(current_user)):
    async with app.state.db.acquire() as conn:
        await conn.execute('DELETE FROM shoe_workbench.remembered_accounts WHERE device_hash=$1 AND account_id<>$2',device_hash(request),user[0].user_id)
    return {'removed':True}


@app.post('/auth/reset-requests',status_code=202)
async def request_password_reset(payload: PasswordRequestInput):
    identifier=payload.identifier.strip()
    if not identifier:
        raise HTTPException(422,'account or name required')
    async with app.state.db.acquire() as conn:
        count=await conn.fetchval("SELECT count(*) FROM shoe_workbench.password_requests WHERE created_at>now()-interval '1 hour'")
        if count>=200:
            raise HTTPException(429,'too many requests')
        await conn.execute('''INSERT INTO shoe_workbench.password_requests(id,identifier,note)
            VALUES($1,$2,$3) ON CONFLICT(lower(identifier)) WHERE status='pending' DO NOTHING''',str(uuid.uuid4()),identifier,payload.note)
    return {'submitted':True}


@app.get('/admin/reset-requests')
async def password_requests(user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        return [dict(r) for r in await conn.fetch("SELECT id,identifier,note,status,created_at FROM shoe_workbench.password_requests WHERE status='pending' ORDER BY created_at LIMIT 200")]


@app.post('/admin/reset-requests/{request_id}/close')
async def close_password_request(request_id: str,user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        await conn.execute("UPDATE shoe_workbench.password_requests SET status='closed',handled_at=now() WHERE id=$1",request_id)
    return {'closed':True}


async def complete_password_requests(username,name):
    async with app.state.db.acquire() as conn:
        await conn.execute("UPDATE shoe_workbench.password_requests SET status='completed',handled_at=now() WHERE status='pending' AND (lower(identifier)=lower($1) OR identifier=$2)",username,name)


@app.post('/admin/local-accounts', status_code=201)
async def create_local_account(payload: LocalAccountInput, user=Depends(current_user)):
    require_admin(user)
    if payload.role not in ('operator', 'management', 'super_admin'):
        raise HTTPException(422, 'invalid role')
    username = payload.username.strip().lower()
    password = str(secrets.randbelow(90000000)+10000000)
    encoded = await asyncio.to_thread(hash_local_password, password)
    account_id = 'shoe-user-' + str(uuid.uuid4())
    async with app.state.db.acquire() as conn:
        try:
            await conn.execute('''INSERT INTO shoe_workbench.local_accounts(id,username,display_name,role_code,category,password_hash)
              VALUES($1,$2,$3,$4,$5,$6)''',account_id,username,payload.display_name.strip(),payload.role,payload.category.strip(),encoded)
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, 'username already exists')
    await credential_vault.store(app.state.db,app.state.accounts,account_id,password)
    response = JSONResponse({'id':account_id,'username':username,'display_name':payload.display_name,'initial_password':password},status_code=201)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.post('/admin/local-accounts/{account_id}/reset-password')
async def reset_local_password(account_id: str, user=Depends(current_user)):
    require_admin(user)
    password = str(secrets.randbelow(90000000)+10000000)
    encoded = await asyncio.to_thread(hash_local_password, password)
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow('''UPDATE shoe_workbench.local_accounts SET password_hash=$2
              WHERE id=$1 AND active RETURNING username,display_name''', account_id, encoded)
            if not row:
                raise HTTPException(404, 'local account not found')
            await conn.execute('DELETE FROM shoe_workbench.local_sessions WHERE user_id=$1', account_id)
            await conn.execute('DELETE FROM shoe_workbench.login_attempts WHERE key=$1', row['username'])
    await credential_vault.store(app.state.db,app.state.accounts,account_id,password)
    response = JSONResponse({'username':row['username'],'display_name':row['display_name'],'initial_password':password})
    await complete_password_requests(row['username'],row['display_name'])
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.patch('/admin/local-accounts/{account_id}')
async def update_local_account(account_id: str, payload: LocalAccountUpdate, user=Depends(current_user)):
    require_admin(user)
    if payload.role not in ('operator', 'management', 'super_admin'):
        raise HTTPException(422, 'invalid role')
    if account_id == user[0].user_id and (not payload.active or payload.role not in ('super_admin','management')):
        raise HTTPException(409, 'cannot disable or demote current account')
    name = payload.display_name.strip()
    if not name:
        raise HTTPException(422, 'name required')
    if payload.username and app.state.accounts:
        async with app.state.accounts.acquire() as conn:
            if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM iam.users WHERE lower(username)=lower($1))',payload.username):
                raise HTTPException(409,'username already exists')
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            if payload.username and await conn.fetchval('SELECT EXISTS(SELECT 1 FROM shoe_workbench.local_accounts WHERE username=$1 AND id<>$2)',payload.username,account_id):
                raise HTTPException(409,'username already exists')
            row = await conn.fetchrow('''UPDATE shoe_workbench.local_accounts
                SET display_name=$2, category=$3, active=$4, role_code=$5,username=COALESCE($6,username) WHERE id=$1
                RETURNING id,username,display_name,category,active,role_code''',
                account_id, name, payload.category.strip(), payload.active, payload.role,payload.username)
            if not row:
                raise HTTPException(404, 'local account not found')
            if not payload.active:
                await conn.execute('DELETE FROM shoe_workbench.local_sessions WHERE user_id=$1', account_id)
            await conn.execute('''UPDATE shoe_workbench.user_profiles SET username=$2,display_name=$3,role_code=$4,category=$5
                WHERE user_id=$1''',account_id,row['username'],row['display_name'],row['role_code'],row['category'])
    return dict(row)


@app.delete('/admin/local-accounts/{account_id}')
async def delete_local_account(account_id: str, user=Depends(current_user)):
    require_admin(user)
    if account_id == user[0].user_id:
        raise HTTPException(409, 'cannot delete current account')
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow('SELECT username FROM shoe_workbench.local_accounts WHERE id=$1 FOR UPDATE', account_id)
            if not row:
                raise HTTPException(404, 'local account not found')
            has_history = await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM shoe_workbench.feedback WHERE author_id=$1)
              OR EXISTS(SELECT 1 FROM shoe_workbench.feedback_messages WHERE author_id=$1)
              OR EXISTS(SELECT 1 FROM shoe_workbench.attachments WHERE owner_id=$1)
              OR EXISTS(SELECT 1 FROM shoe_workbench.events WHERE user_id=$1)
              OR EXISTS(SELECT 1 FROM shoe_workbench.template_versions WHERE published_by=$1)
              OR EXISTS(SELECT 1 FROM shoe_workbench.announcements WHERE created_by=$1 OR audience_user_id=$1)
              OR EXISTS(SELECT 1 FROM shoe_workbench.announcement_reads WHERE user_id=$1)
              OR EXISTS(SELECT 1 FROM shoe_workbench.user_preferences WHERE user_id=$1)''', account_id)
            if has_history:
                raise HTTPException(409, 'account has history; disable instead')
            await conn.execute('DELETE FROM shoe_workbench.local_sessions WHERE user_id=$1', account_id)
            await conn.execute('DELETE FROM shoe_workbench.user_profiles WHERE user_id=$1', account_id)
            await conn.execute('DELETE FROM shoe_workbench.login_attempts WHERE key=$1', row['username'])
            await conn.execute('DELETE FROM shoe_workbench.local_accounts WHERE id=$1', account_id)
    return {'deleted': True}


@app.get('/health')
async def health():
    async with app.state.db.acquire() as conn:
        await conn.fetchval('SELECT 1')
    return {'ok': True}


async def account_metadata(row, category, actor_id, action):
    async with app.state.db.acquire() as conn:
        await conn.execute('''INSERT INTO shoe_workbench.user_profiles(user_id,username,display_name,role_code,last_seen,category)
            VALUES($1,$2,$3,$4,NULL,$5) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
            display_name=excluded.display_name,role_code=excluded.role_code,category=excluded.category,removed_at=NULL''',
            row['user_id'],row['username'],row['display_name'],row['role_code'],category)
        await conn.execute('INSERT INTO shoe_workbench.account_audit(actor_id,target_id,action) VALUES($1,$2,$3)',actor_id,row['user_id'],action)


@app.post('/admin/accounts',status_code=201)
async def create_shared_account(payload: AccountInput,user=Depends(current_user)):
    require_admin(user)
    if not app.state.accounts:
        raise HTTPException(503,'identity management unavailable')
    async with app.state.db.acquire() as conn:
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM shoe_workbench.local_accounts WHERE lower(username)=lower($1))',payload.username):
            raise HTTPException(409,'username already exists')
    password = f'{secrets.randbelow(1000000):06d}'
    row = await shared_accounts.save(app.state.accounts,None,payload.username,payload.display_name.strip(),payload.role,True,password)
    await account_metadata(row,payload.category,user[0].user_id,'create')
    await credential_vault.store(app.state.db,app.state.accounts,row['user_id'],password)
    response=JSONResponse(dict(row,initial_password=password),status_code=201)
    response.headers['Cache-Control']='no-store'
    return response


@app.patch('/admin/accounts/{account_id}')
async def update_shared_account(account_id: int,payload: AccountInput,user=Depends(current_user)):
    require_admin(user)
    if str(account_id)==user[0].user_id and (not payload.active or payload.role not in ('super_admin','management')):
        raise HTTPException(409,'cannot disable or demote current account')
    if not payload.display_name.strip():
        raise HTTPException(422,'name required')
    async with app.state.db.acquire() as conn:
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM shoe_workbench.local_accounts WHERE lower(username)=lower($1))',payload.username):
            raise HTTPException(409,'username already exists')
    row = await shared_accounts.save(app.state.accounts,account_id,payload.username,payload.display_name.strip(),payload.role,payload.active)
    await account_metadata(row,payload.category,user[0].user_id,'update')
    return row


@app.post('/admin/accounts/{account_id}/reset-password')
async def reset_shared_account(account_id: int,user=Depends(current_user)):
    require_admin(user)
    result = await shared_accounts.reset(app.state.accounts,account_id)
    await credential_vault.store(app.state.db,app.state.accounts,account_id,result['initial_password'])
    await complete_password_requests(result['username'],result['display_name'])
    async with app.state.db.acquire() as conn:
        await conn.execute('INSERT INTO shoe_workbench.account_audit(actor_id,target_id,action) VALUES($1,$2,$3)',user[0].user_id,str(account_id),'reset_password')
    response=JSONResponse(result)
    response.headers['Cache-Control']='no-store'
    return response


@app.get('/admin/account-credentials/{account_id}')
async def reveal_account_credential(account_id: str,user=Depends(current_user)):
    require_admin(user)
    result=await credential_vault.reveal(app.state.db,app.state.accounts,account_id)
    if not result:
        raise HTTPException(409,'当前密码尚未核对保存，或已从其他系统修改。请重置后查看。')
    async with app.state.db.acquire() as conn:
        await conn.execute('INSERT INTO shoe_workbench.account_audit(actor_id,target_id,action) VALUES($1,$2,$3)',user[0].user_id,account_id,'view_credential')
    return JSONResponse(result,headers={'Cache-Control':'no-store'})


@app.delete('/admin/accounts/{account_id}')
async def delete_shared_account(account_id: int,user=Depends(current_user)):
    require_admin(user)
    if str(account_id)==user[0].user_id:
        raise HTTPException(409,'cannot delete current account')
    # Workbench history is retained under the stable person ID, never cascaded.
    await shared_accounts.delete(app.state.accounts,account_id)
    async with app.state.db.acquire() as conn:
        await conn.execute('UPDATE shoe_workbench.user_profiles SET removed_at=now() WHERE user_id=$1',str(account_id))
        await conn.execute('INSERT INTO shoe_workbench.account_audit(actor_id,target_id,action) VALUES($1,$2,$3)',user[0].user_id,str(account_id),'delete')
    return {'deleted':True}


@app.get('/me')
async def me(user=Depends(current_user)):
    actor, principal = user
    async with app.state.db.acquire() as conn:
        await conn.execute('''INSERT INTO shoe_workbench.user_profiles(user_id,username,display_name,role_code,last_seen)
          VALUES($1,$2,$3,$4,now()) ON CONFLICT(user_id) DO UPDATE SET
          username=excluded.username,display_name=excluded.display_name,role_code=excluded.role_code,last_seen=now()''',
          actor.user_id,principal.username,principal.display_name,principal.role)
    return {'id': actor.user_id, 'username': principal.username,
            'name': principal.display_name, 'admin': actor.is_admin}


@app.get('/selling-points')
async def selling_points(user=Depends(current_user)):
    async with app.state.db.acquire() as conn:
        rows = await conn.fetch('''SELECT id,title,description,position FROM shoe_workbench.selling_points
            WHERE archived_at IS NULL ORDER BY position,id''')
    return [dict(row) for row in rows]


@app.put('/admin/selling-points/{point_id}')
async def save_selling_point(point_id: str, payload: SellingPointInput, user=Depends(current_user)):
    require_admin(user)
    if not point_id or len(point_id) > 100:
        raise HTTPException(422, 'invalid selling point id')
    async with app.state.db.acquire() as conn:
        await conn.execute('''INSERT INTO shoe_workbench.selling_points(id,title,description,position)
          VALUES($1,$2,$3,$4) ON CONFLICT(id) DO UPDATE SET title=excluded.title,
          description=excluded.description,position=excluded.position,updated_at=now()''',
          point_id,payload.title,payload.description,payload.position)
    return {'id': point_id, 'saved': True}


@app.post('/admin/selling-points/{point_id}/archive')
async def archive_selling_point(point_id: str, user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            point = await conn.fetchval('SELECT id FROM shoe_workbench.selling_points WHERE id=$1 AND archived_at IS NULL FOR UPDATE', point_id)
            if point is None:
                raise HTTPException(404, 'selling point not found')
            used = await conn.fetchval('SELECT EXISTS(SELECT 1 FROM shoe_workbench.templates WHERE selling_point_id=$1 AND archived_at IS NULL)', point_id)
            if used:
                raise HTTPException(409, 'archive or move its templates first')
            await conn.execute('UPDATE shoe_workbench.selling_points SET archived_at=now(),updated_at=now() WHERE id=$1', point_id)
    return {'archived': True}


@app.put('/admin/selling-points-order')
async def reorder_selling_points(payload: OrderInput, user=Depends(current_user)):
    require_admin(user)
    if len(set(payload.ids)) != len(payload.ids):
        raise HTTPException(422, 'duplicate ids')
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch('SELECT id FROM shoe_workbench.selling_points WHERE archived_at IS NULL FOR UPDATE')
            if {row['id'] for row in rows} != set(payload.ids):
                raise HTTPException(409, 'selling points changed; refresh')
            await conn.executemany('UPDATE shoe_workbench.selling_points SET position=$2,updated_at=now() WHERE id=$1',
                                   [(ident,index) for index,ident in enumerate(payload.ids,1)])
    return {'saved': True}


@app.put('/admin/templates-order')
async def reorder_templates(payload: OrderInput, user=Depends(current_user)):
    require_admin(user)
    if len(set(payload.ids)) != len(payload.ids):
        raise HTTPException(422, 'duplicate ids')
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch('SELECT id,selling_point_id FROM shoe_workbench.templates WHERE id=ANY($1::text[]) AND archived_at IS NULL FOR UPDATE',payload.ids)
            if len(rows) != len(payload.ids) or len({row['selling_point_id'] for row in rows}) > 1:
                raise HTTPException(409, 'templates changed; refresh')
            await conn.executemany('UPDATE shoe_workbench.templates SET position=$2,updated_at=now() WHERE id=$1',
                                   [(ident,index) for index,ident in enumerate(payload.ids,1)])
    return {'saved': True}


@app.get('/preferences')
async def get_preferences(user=Depends(current_user)):
    async with app.state.db.acquire() as conn:
        value = await conn.fetchval('SELECT preferences FROM shoe_workbench.user_preferences WHERE user_id=$1', user[0].user_id)
    return json_value(value) if value is not None else {}


@app.put('/preferences')
async def set_preferences(preferences: dict, user=Depends(current_user)):
    encoded = json.dumps(preferences, ensure_ascii=False)
    if len(encoded.encode('utf-8')) > 100_000:
        raise HTTPException(413, 'preferences too large')
    async with app.state.db.acquire() as conn:
        await conn.execute('''INSERT INTO shoe_workbench.user_preferences(user_id,preferences)
            VALUES($1,$2::jsonb) ON CONFLICT(user_id) DO UPDATE
            SET preferences=excluded.preferences,updated_at=now()''', user[0].user_id, encoded)
    return {'saved': True}


@app.post('/events', status_code=201)
async def add_event(payload: EventInput, user=Depends(current_user)):
    if payload.event_type not in ('template_open', 'prompt_copy', 'version_select', 'favorite_add', 'favorite_remove'):
        raise HTTPException(422, 'invalid event type')
    async with app.state.db.acquire() as conn:
        exists = await conn.fetchval('SELECT EXISTS(SELECT 1 FROM shoe_workbench.templates WHERE id=$1 AND published_version IS NOT NULL AND archived_at IS NULL)', payload.template_id)
        if not exists:
            raise HTTPException(404, 'template not found')
        await conn.execute('''INSERT INTO shoe_workbench.events(id,user_id,event_type,template_id,template_version)
          VALUES($1,$2,$3,$4,$5)''', str(uuid.uuid4()),user[0].user_id,payload.event_type,payload.template_id,payload.template_version)
        if payload.event_type=='prompt_copy' and payload.template_version and payload.field_values:
            version=await conn.fetchval('SELECT content FROM shoe_workbench.template_versions WHERE template_id=$1 AND version=$2',payload.template_id,payload.template_version)
            content=json_value(version) if version else {}
            if isinstance(content.get('content'),dict):
                content=content['content']
            fields={str(f['index']):f for f in content.get('bracketFields',[]) if f.get('mode')!='fixed' and f.get('control') not in ('textarea','switch')}
            labels={'shoeType':'鞋子类型','scene':'场景','season':'季节','sellingPoint':'卖点','count':'方案数量'}
            for key in content.get('fields',[]):
                if isinstance(key,str) and key in labels:
                    fields[key]={'label':labels[key],'options':[]}
            for item in {(v.key,v.value):v for v in payload.field_values}.values():
                field=fields.get(item.key)
                value=' '.join(item.value.split()).strip()
                if not field or not value or len(value)>60:
                    continue
                source='preset' if value in field.get('options',[]) else 'custom'
                await conn.execute('''INSERT INTO shoe_workbench.field_usage(user_id,template_id,template_version,field_key,field_label,value,source)
                    VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(user_id,template_id,template_version,field_key,value)
                    DO UPDATE SET use_count=CASE WHEN shoe_workbench.field_usage.stats_excluded THEN 1 ELSE shoe_workbench.field_usage.use_count+1 END,
                    stats_excluded=false,last_used=now()''',
                    user[0].user_id,payload.template_id,payload.template_version,item.key,str(field.get('label',item.key))[:100],value,source)
            # Keep at most 100 short values per person and field; discard stale suggestions.
            await conn.execute('''DELETE FROM shoe_workbench.field_usage WHERE user_id=$1 AND template_id=$2 AND
                (last_used<now()-interval '180 days' OR (template_version,field_key,value) IN (
                SELECT template_version,field_key,value FROM (SELECT template_version,field_key,value,
                row_number() OVER(PARTITION BY template_version,field_key ORDER BY last_used DESC) rn
                FROM shoe_workbench.field_usage WHERE user_id=$1 AND template_id=$2) ranked WHERE rn>100))''',user[0].user_id,payload.template_id)
    return {'recorded': True}


@app.get('/templates/{template_id}/suggestions')
async def personal_suggestions(template_id: str,version: int,user=Depends(current_user)):
    async with app.state.db.acquire() as conn:
        rows=await conn.fetch('''SELECT field_key,value,use_count FROM shoe_workbench.field_usage
            WHERE user_id=$1 AND template_id=$2 AND template_version=$3 AND use_count>=3
            AND NOT stats_excluded AND source='custom' AND last_used>now()-interval '180 days' ORDER BY use_count DESC,last_used DESC LIMIT 60''',user[0].user_id,template_id,version)
    return [dict(r) for r in rows]


@app.get('/admin/usage-dashboard')
async def usage_dashboard(days: int=30,user=Depends(current_user)):
    require_admin(user)
    if days not in (7,30,90):
        raise HTTPException(422,'invalid period')
    scope=""" FROM shoe_workbench.events e JOIN shoe_workbench.templates t ON t.id=e.template_id
        LEFT JOIN shoe_workbench.template_versions v ON v.template_id=e.template_id AND v.version=e.template_version
        WHERE e.created_at>=now()-($1*interval '1 day') AND NOT e.stats_excluded
        AND t.archived_at IS NULL AND v.deleted_at IS NULL """
    async with app.state.db.acquire() as conn:
        totals=await conn.fetchrow("""SELECT count(DISTINCT e.user_id) AS active_users,
            count(*) FILTER(WHERE e.event_type='template_open') AS opens,
            count(*) FILTER(WHERE e.event_type='prompt_copy') AS copies,
            count(*) FILTER(WHERE e.event_type='version_select') AS version_choices,
            count(*) FILTER(WHERE e.event_type='favorite_add') AS favorites_added,
            count(*) FILTER(WHERE e.event_type='favorite_remove') AS favorites_removed"""+scope,days)
        online=await conn.fetchval("SELECT count(*) FROM shoe_workbench.user_profiles WHERE removed_at IS NULL AND last_seen>now()-interval '90 seconds'")
        rows=await conn.fetch("""SELECT e.user_id,
            (SELECT display_name FROM shoe_workbench.user_profiles WHERE user_id=e.user_id) AS display_name,
            (SELECT username FROM shoe_workbench.user_profiles WHERE user_id=e.user_id) AS username,
            e.template_id,t.title,e.template_version,COALESCE(v.display_name,'v'||e.template_version::text) AS version_name,
            count(*) FILTER(WHERE e.event_type='template_open') AS opens,
            count(*) FILTER(WHERE e.event_type='prompt_copy') AS copies,
            count(*) FILTER(WHERE e.event_type='version_select') AS version_choices,
            count(*) FILTER(WHERE e.event_type='favorite_add') AS favorites_added,
            count(*) FILTER(WHERE e.event_type='favorite_remove') AS favorites_removed,
            max(e.created_at) AS last_used"""+scope+"""
            GROUP BY e.user_id,e.template_id,t.title,e.template_version,v.display_name
            ORDER BY copies DESC,opens DESC LIMIT 5000""",days)
        trend=await conn.fetch("""SELECT (e.created_at AT TIME ZONE 'Asia/Shanghai')::date AS day,e.user_id,e.template_id,
            count(*) FILTER(WHERE e.event_type='template_open') AS opens,
            count(*) FILTER(WHERE e.event_type='prompt_copy') AS copies,count(DISTINCT e.user_id) AS users"""+scope+"""
            GROUP BY 1,e.user_id,e.template_id ORDER BY 1 LIMIT 20000""",days)
        words=await conn.fetch("""SELECT u.user_id,u.template_id,t.title,u.template_version,u.field_key,u.field_label,u.value,u.source,
            COALESCE(v.display_name,'v'||u.template_version::text) AS version_name,u.use_count AS uses
            FROM shoe_workbench.field_usage u JOIN shoe_workbench.templates t ON t.id=u.template_id
            LEFT JOIN shoe_workbench.template_versions v ON v.template_id=u.template_id AND v.version=u.template_version
            WHERE u.last_used>=now()-($1*interval '1 day') AND NOT u.stats_excluded
            AND t.archived_at IS NULL AND v.deleted_at IS NULL ORDER BY uses DESC LIMIT 5000""",days)
        favorites=await conn.fetch("""SELECT p.user_id,f.value AS template_id FROM shoe_workbench.user_preferences p
            CROSS JOIN LATERAL jsonb_array_elements_text(CASE WHEN jsonb_typeof(p.preferences->'favorites')='array'
            THEN p.preferences->'favorites' ELSE '[]'::jsonb END) f(value)
            JOIN shoe_workbench.templates t ON t.id=f.value WHERE t.archived_at IS NULL LIMIT 10000""")
    return {'days':days,'as_of':datetime.now(timezone.utc).isoformat(),'totals':dict(totals,online=online),'rows':[dict(r) for r in rows],
            'trend':[dict(r) for r in trend],'words':[dict(r) for r in words],'favorites':[dict(r) for r in favorites],
            'word_scope':'累计短选项使用次数；已排除删除版本、归档模板和手动清理的记录'}


@app.get('/admin/events/summary')
async def event_summary(user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        rows = await conn.fetch('''SELECT template_id,event_type,template_version,count(*) AS event_count,
          count(DISTINCT user_id) AS user_count FROM shoe_workbench.events
          GROUP BY template_id,event_type,template_version ORDER BY template_id,event_type,template_version''')
    return [dict(row) for row in rows]


@app.get('/admin/users/activity')
async def user_activity(user=Depends(current_user)):
    require_admin(user)
    shared = await shared_accounts.directory(app.state.accounts) if app.state.accounts else []
    async with app.state.db.acquire() as conn:
        for person in shared:
            await conn.execute('''INSERT INTO shoe_workbench.user_profiles(user_id,username,display_name,role_code,last_seen)
                VALUES($1,$2,$3,$4,NULL) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                display_name=excluded.display_name,role_code=excluded.role_code,removed_at=NULL''',
                person['user_id'],person['username'],person['display_name'],person['role_code'])
        rows = await conn.fetch('''SELECT u.user_id,u.username,u.display_name,u.role_code,u.last_seen,u.category,u.active,
          count(e.id) FILTER (WHERE e.event_type='template_open') AS opens,
          count(e.id) FILTER (WHERE e.event_type='prompt_copy') AS copies,
          count(e.id) FILTER (WHERE e.event_type='version_select') AS version_choices
          FROM (SELECT p.user_id,p.username,p.display_name,p.role_code,p.last_seen,
                       COALESCE(p.category,CASE p.role_code WHEN 'operator' THEN '运营' WHEN 'ai' THEN 'AI' WHEN 'management' THEN '管理' WHEN 'super_admin' THEN '管理' ELSE '' END) AS category, true AS active
                  FROM shoe_workbench.user_profiles p WHERE p.removed_at IS NULL AND NOT EXISTS (SELECT 1 FROM shoe_workbench.local_accounts a WHERE a.id=p.user_id)
                UNION ALL SELECT a.id,a.username,a.display_name,a.role_code,p.last_seen,a.category,a.active
                  FROM shoe_workbench.local_accounts a LEFT JOIN shoe_workbench.user_profiles p ON p.user_id=a.id) u
          LEFT JOIN shoe_workbench.events e ON e.user_id=u.user_id AND NOT e.stats_excluded
            AND EXISTS(SELECT 1 FROM shoe_workbench.templates t WHERE t.id=e.template_id AND t.archived_at IS NULL)
            AND NOT EXISTS(SELECT 1 FROM shoe_workbench.template_versions v WHERE v.template_id=e.template_id AND v.version=e.template_version AND v.deleted_at IS NOT NULL)
          GROUP BY u.user_id,u.username,u.display_name,u.role_code,u.last_seen,u.category,u.active ORDER BY u.active DESC,u.last_seen DESC NULLS LAST''')
    identities={p['user_id']:p for p in shared}
    result=[]
    for row in rows:
        item=dict(row)
        if item['user_id'] in identities:
            item['active']=identities[item['user_id']]['status']=='active'
        item['source']='local' if item['user_id'].startswith('shoe-user-') else 'shared'
        result.append(item)
    return sorted(result,key=lambda r:not r['active'])


@app.get('/admin/events/users')
async def user_template_activity(user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        rows = await conn.fetch('''SELECT e.user_id,p.username,p.display_name,e.template_id,e.template_version,
          count(*) FILTER (WHERE e.event_type='template_open') AS opens,
          count(*) FILTER (WHERE e.event_type='prompt_copy') AS copies,
          count(*) FILTER (WHERE e.event_type='version_select') AS version_choices,
          max(e.created_at) AS last_used
          FROM shoe_workbench.events e LEFT JOIN shoe_workbench.user_profiles p ON p.user_id=e.user_id
          WHERE NOT e.stats_excluded AND EXISTS(SELECT 1 FROM shoe_workbench.templates t WHERE t.id=e.template_id AND t.archived_at IS NULL)
            AND NOT EXISTS(SELECT 1 FROM shoe_workbench.template_versions v WHERE v.template_id=e.template_id AND v.version=e.template_version AND v.deleted_at IS NOT NULL)
          GROUP BY e.user_id,p.username,p.display_name,e.template_id,e.template_version
          ORDER BY max(e.created_at) DESC LIMIT 1000''')
    return [dict(row) for row in rows]


@app.get('/help')
async def get_help(user=Depends(current_user)):
    async with app.state.db.acquire() as conn:
        value = await conn.fetchval("SELECT value FROM shoe_workbench.settings WHERE key='help'")
    return json_value(value) if value is not None else {'text': '', 'images': []}


@app.get('/feedback-types')
async def get_feedback_types(user=Depends(current_user)):
    async with app.state.db.acquire() as conn:
        value = await conn.fetchval("SELECT value FROM shoe_workbench.settings WHERE key='feedback_types'")
    return json_value(value) if value is not None else []


@app.put('/admin/feedback-types')
async def save_feedback_types(payload: list[FeedbackType], user=Depends(current_user)):
    require_admin(user)
    if len(payload) > 100 or len({item.id for item in payload}) != len(payload):
        raise HTTPException(422, 'invalid feedback types')
    async with app.state.db.acquire() as conn:
        await conn.execute('''INSERT INTO shoe_workbench.settings(key,value,updated_by) VALUES('feedback_types',$1::jsonb,$2)
          ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=now()''',
          json.dumps([item.model_dump() for item in payload],ensure_ascii=False),user[0].user_id)
    return {'saved': True}


@app.put('/admin/help')
async def save_help(payload: HelpInput, user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        count = await conn.fetchval('''SELECT count(*) FROM shoe_workbench.attachments
          WHERE id=ANY($1::text[]) AND feedback_message_id IS NULL''', payload.images)
        if count != len(set(payload.images)):
            raise HTTPException(422, 'unknown image')
        await conn.execute('''INSERT INTO shoe_workbench.settings(key,value,updated_by) VALUES('help',$1::jsonb,$2)
          ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=now()''',
          json.dumps(payload.model_dump(),ensure_ascii=False),user[0].user_id)
    return {'saved': True}


@app.post('/admin/help/images', status_code=201)
async def upload_help_image(request: Request, user=Depends(current_user)):
    require_admin(user)
    root_setting = os.environ.get('SHOE_UPLOAD_DIR')
    if not root_setting:
        raise HTTPException(503, 'upload storage is not configured')
    content_type, data = await media_support.read_upload(request)
    ident = str(uuid.uuid4())
    root = Path(root_setting).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / ident
    try:
        with path.open('xb') as output:
            output.write(data)
        os.chmod(path,0o600)
        async with app.state.db.acquire() as conn:
            await conn.execute('''INSERT INTO shoe_workbench.attachments(id,owner_id,storage_key,content_type,byte_size)
              VALUES($1,$2,$1,$3,$4)''',ident,user[0].user_id,content_type,len(data))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'id': ident}


@app.get('/help/images/{image_id}')
async def read_help_image(image_id: str, user=Depends(current_user)):
    try:
        ident = str(uuid.UUID(image_id))
    except ValueError:
        raise HTTPException(404, 'image not found')
    root_setting = os.environ.get('SHOE_UPLOAD_DIR')
    if not root_setting:
        raise HTTPException(503, 'upload storage is not configured')
    async with app.state.db.acquire() as conn:
        row = await conn.fetchrow('''SELECT a.storage_key,a.content_type FROM shoe_workbench.attachments a
          WHERE a.id=$1 AND a.feedback_message_id IS NULL AND
            EXISTS(SELECT 1 FROM shoe_workbench.help_articles h WHERE h.images ? a.id AND (h.status='published' OR $2))''',ident,user[0].is_admin)
    path = Path(root_setting).resolve() / ident
    if row is None or row['storage_key'] != ident or not path.is_file():
        raise HTTPException(404, 'image not found')
    return FileResponse(path,media_type=row['content_type'],headers={'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})


@app.get('/templates')
async def templates(user=Depends(current_user)):
    async with app.state.db.acquire() as conn:
        rows = await conn.fetch('''SELECT t.id,t.selling_point_id,t.title,t.media_type,t.position,
            v.version,v.content,COALESCE(v.display_name,'v'||v.version::text) AS version_name,v.revision FROM shoe_workbench.templates t
            JOIN shoe_workbench.template_versions v ON v.template_id=t.id AND v.version=t.published_version
            WHERE t.archived_at IS NULL ORDER BY t.position,t.id''')
    return [{'id': r['id'], 'selling_point_id': r['selling_point_id'], 'title': r['title'],
             'media_type': r['media_type'], 'position': r['position'], 'version': r['version'],
             'content': json.loads(r['content']) if isinstance(r['content'], str) else r['content'],
             'version_name':r['version_name'],'revision':r['revision']} for r in rows]


@app.get('/templates/{template_id}/versions')
async def template_versions(template_id: str, user=Depends(current_user)):
    async with app.state.db.acquire() as conn:
        rows = await conn.fetch('''SELECT v.version,v.content,v.published_at,COALESCE(v.display_name,'v'||v.version::text) AS version_name,v.revision FROM shoe_workbench.template_versions v
            JOIN shoe_workbench.templates t ON t.id=v.template_id WHERE v.template_id=$1 AND t.archived_at IS NULL AND v.deleted_at IS NULL
            ORDER BY v.version DESC''', template_id)
    return [{'version': row['version'], 'content': json_value(row['content']), 'published_at': row['published_at'],
             'version_name':row['version_name'],'revision':row['revision']} for row in rows]


@app.patch('/admin/templates/{template_id}/versions/{version}')
async def rename_template_version(template_id: str,version: int,payload: VersionName,user=Depends(current_user)):
    require_admin(user)
    name=payload.name.strip()
    if not name:
        raise HTTPException(422,'请输入版本名称')
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval('SELECT id FROM shoe_workbench.templates WHERE id=$1 AND archived_at IS NULL FOR UPDATE',template_id):
                raise HTTPException(404,'template not found')
            duplicate=await conn.fetchval("SELECT EXISTS(SELECT 1 FROM shoe_workbench.template_versions WHERE template_id=$1 AND version<>$2 AND deleted_at IS NULL AND lower(COALESCE(display_name,'v'||version::text))=lower($3))",template_id,version,name)
            if duplicate:
                raise HTTPException(409,'该模板已有同名版本，请修改名称')
            result=await conn.fetchval('UPDATE shoe_workbench.template_versions SET display_name=$3,updated_at=now() WHERE template_id=$1 AND version=$2 AND deleted_at IS NULL RETURNING version',template_id,version,name)
            if result is None:
                raise HTTPException(404,'version not found')
    return {'version':version,'version_name':name}


@app.post('/admin/templates/{template_id}/usage/reset')
async def reset_template_usage(template_id: str,version: int | None=None,user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM shoe_workbench.templates WHERE id=$1)',template_id):
                raise HTTPException(404,'template not found')
            await conn.execute('UPDATE shoe_workbench.events SET stats_excluded=true WHERE template_id=$1 AND ($2::integer IS NULL OR template_version=$2)',template_id,version)
            await conn.execute('UPDATE shoe_workbench.field_usage SET stats_excluded=true WHERE template_id=$1 AND ($2::integer IS NULL OR template_version=$2)',template_id,version)
    return {'cleared':True,'version':version}


@app.delete('/admin/templates/{template_id}/versions/{version}')
async def delete_template_version(template_id: str, version: int, user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow('SELECT published_version FROM shoe_workbench.templates WHERE id=$1 AND archived_at IS NULL FOR UPDATE', template_id)
            if current is None:
                raise HTTPException(404, 'template not found')
            if version == current['published_version']:
                raise HTTPException(409, '当前发布版本不能删除，请先发布替代版本')
            removed = await conn.fetchval('''UPDATE shoe_workbench.template_versions SET deleted_at=now(),deleted_by=$3
                WHERE template_id=$1 AND version=$2 AND deleted_at IS NULL RETURNING version''',template_id,version,user[0].user_id)
            if removed is None:
                raise HTTPException(404, '版本不存在或已删除')
    return {'deleted': True, 'version': version}


@app.get('/admin/templates/{template_id}/draft')
async def get_template_draft(template_id: str, user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        row = await conn.fetchrow('SELECT draft,published_version FROM shoe_workbench.templates WHERE id=$1 AND archived_at IS NULL', template_id)
    if row is None:
        raise HTTPException(404, 'template not found')
    return {'draft': json_value(row['draft']), 'published_version': row['published_version']}


@app.get('/admin/templates')
async def admin_templates(user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        rows = await conn.fetch('''SELECT id,title,media_type,selling_point_id,position,draft,published_version,updated_at
            FROM shoe_workbench.templates WHERE archived_at IS NULL ORDER BY position,id''')
    result = []
    for row in rows:
        item = dict(row)
        item['draft'] = json_value(item['draft'])
        result.append(item)
    return result


@app.delete('/admin/templates/{template_id}/draft')
async def discard_template_draft(template_id: str,user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        result=await conn.fetchval('UPDATE shoe_workbench.templates SET draft=NULL,updated_at=now() WHERE id=$1 AND archived_at IS NULL RETURNING id',template_id)
    if result is None:
        raise HTTPException(404,'template not found')
    return {'discarded':True}


@app.post('/admin/templates/{template_id}/archive')
async def archive_template(template_id: str, user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        row = await conn.fetchval('''UPDATE shoe_workbench.templates SET archived_at=now(),updated_at=now()
            WHERE id=$1 AND archived_at IS NULL RETURNING id''', template_id)
    if row is None:
        raise HTTPException(404, 'template not found')
    return {'archived': True}


@app.put('/admin/templates/{template_id}/draft')
async def save_template_draft(template_id: str, payload: TemplateDraft, user=Depends(current_user)):
    require_admin(user)
    if payload.media_type not in ('image', 'video'):
        raise HTTPException(422, 'invalid media type')
    if len(template_id) > 100 or not template_id:
        raise HTTPException(422, 'invalid template id')
    validate_template_content(payload.content)
    async with app.state.db.acquire() as conn:
        await conn.execute('''INSERT INTO shoe_workbench.templates
            (id,title,media_type,selling_point_id,draft)
            VALUES ($1,$2,$3,$4,$5::jsonb)
            ON CONFLICT(id) DO UPDATE SET draft=excluded.draft,updated_at=now()''',
            template_id, payload.title, payload.media_type, payload.selling_point_id,
            json.dumps(payload.model_dump(), ensure_ascii=False))
    return {'id': template_id, 'saved': True}


@app.post('/admin/templates/{template_id}/publish')
async def publish_template(template_id: str, update_current: bool=False,user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow('SELECT draft,published_version FROM shoe_workbench.templates WHERE id=$1 AND archived_at IS NULL FOR UPDATE', template_id)
            if row is None:
                raise HTTPException(404, 'template not found')
            if row['draft'] is None:
                raise HTTPException(409, 'template has no draft')
            draft = json.loads(row['draft']) if isinstance(row['draft'], str) else row['draft']
            validate_template_content(draft['content'], publish=True)
            if update_current:
                version=row['published_version']
                if not version:
                    raise HTTPException(409,'首次发布请使用发布新版本')
                old=await conn.fetchrow('SELECT content,revision FROM shoe_workbench.template_versions WHERE template_id=$1 AND version=$2 FOR UPDATE',template_id,version)
                await conn.execute('''INSERT INTO shoe_workbench.version_revisions(template_id,version,revision,content,changed_by)
                    VALUES($1,$2,$3,$4::jsonb,$5) ON CONFLICT DO NOTHING''',template_id,version,old['revision'],json.dumps(json_value(old['content']),ensure_ascii=False),user[0].user_id)
                await conn.execute('UPDATE shoe_workbench.template_versions SET content=$3::jsonb,revision=revision+1,updated_at=now() WHERE template_id=$1 AND version=$2',template_id,version,json.dumps(draft['content'],ensure_ascii=False))
            else:
                version = (row['published_version'] or 0) + 1
                labels=await conn.fetch("SELECT COALESCE(display_name,'v'||version::text) AS name FROM shoe_workbench.template_versions WHERE template_id=$1 AND deleted_at IS NULL",template_id)
                next_name='v'+str(max([int(r['name'][1:]) for r in labels if re.fullmatch(r'v[0-9]+',r['name'])]+[0])+1)
                await conn.execute('''INSERT INTO shoe_workbench.template_versions
                    (template_id,version,content,published_by,display_name) VALUES($1,$2,$3::jsonb,$4,$5)''',
                    template_id, version, json.dumps(draft['content'], ensure_ascii=False), user[0].user_id,next_name)
            await conn.execute('''UPDATE shoe_workbench.templates SET published_version=$2,draft=NULL,
                title=$3,media_type=$4,selling_point_id=$5,updated_at=now() WHERE id=$1''',
                template_id, version, draft['title'], draft['media_type'], draft.get('selling_point_id'))
            meta=await conn.fetchrow("SELECT COALESCE(display_name,'v'||version::text) AS version_name,revision FROM shoe_workbench.template_versions WHERE template_id=$1 AND version=$2",template_id,version)
    return {'id': template_id, 'version': version,'version_name':meta['version_name'],'revision':meta['revision'],'updated_current':update_current}


@app.post('/feedback', status_code=201)
async def create_feedback(payload: FeedbackCreate, user=Depends(current_user)):
    actor, _ = user
    ident = str(payload.submission_id or uuid.uuid4())
    async with app.state.db.acquire() as conn:
        existing=await conn.fetchrow('SELECT author_id,deleted_at FROM shoe_workbench.feedback WHERE id=$1',ident)
        if existing:
            if existing['author_id']!=actor.user_id:
                raise HTTPException(409,'submission id already used')
            if existing['deleted_at']:
                raise HTTPException(409,'submission already deleted')
            return {'id':ident,'status':'pending','already_saved':True}
        reference=None
        if payload.template_id:
            linked=await conn.fetchrow('''SELECT t.id,t.title,v.version,COALESCE(v.display_name,'v'||v.version::text) AS version_name
                FROM shoe_workbench.templates t JOIN shoe_workbench.template_versions v ON v.template_id=t.id AND v.version=COALESCE($2,t.published_version)
                WHERE t.id=$1 AND t.archived_at IS NULL AND v.deleted_at IS NULL''',payload.template_id,payload.template_version)
            if not linked:raise HTTPException(422,'引用模板或版本已删除，请重新选择')
            reference=json.dumps(dict(linked))
        if payload.announcement_id and not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM shoe_workbench.announcements WHERE id=$1 AND status='published' AND deleted_at IS NULL AND audience_user_id IS NULL)",payload.announcement_id):
            raise HTTPException(404,'announcement not found')
        await conn.execute('''INSERT INTO shoe_workbench.feedback
          (id, author_id, template_id, category, content, anonymous_to_users,announcement_id,template_reference)
          VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) ON CONFLICT(id) DO NOTHING''', ident, actor.user_id, payload.template_id,
          payload.category, payload.content, payload.anonymous_to_users,payload.announcement_id,reference)
        await collaboration.notify_admins(conn,app.state.accounts,ident,actor,'收到新的需求反馈')
    return {'id': ident, 'status': 'pending'}


@app.get('/feedback')
async def list_feedback(offset: int=0,user=Depends(current_user)):
    if offset<0:
        raise HTTPException(422,'invalid offset')
    actor, principal = user
    async with app.state.db.acquire() as conn:
        rows = await conn.fetch('''SELECT f.id,f.author_id,f.template_id,f.category,f.content,f.template_reference,
          f.anonymous_to_users,f.status,f.created_at,f.updated_at,f.resolved_at,p.display_name AS author_name,
          f.announcement_id,a.title AS announcement_title
          FROM shoe_workbench.feedback f LEFT JOIN shoe_workbench.user_profiles p ON p.user_id=f.author_id
          LEFT JOIN shoe_workbench.announcements a ON a.id=f.announcement_id
          WHERE f.deleted_at IS NULL AND (f.status != 'withdrawn' OR f.author_id=$1 OR $2)
          ORDER BY f.created_at DESC,f.id DESC LIMIT 50 OFFSET $3''', actor.user_id, actor.is_admin,offset)
    result = []
    for row in rows:
        item = dict(row)
        # Even stable author IDs are identifying information for anonymous feedback.
        async with app.state.db.acquire() as conn:
            item=await collaboration.decorate(conn,item,actor,item['id'],row['anonymous_to_users'])
        result.append(item)
    return result


@app.patch('/feedback/{feedback_id}')
async def edit_feedback(feedback_id: str, payload: FeedbackEdit, user=Depends(current_user)):
    async with app.state.db.acquire() as conn:
        result = await conn.fetchval('''UPDATE shoe_workbench.feedback SET content=$3,updated_at=now()
            WHERE id=$1 AND author_id=$2 AND deleted_at IS NULL AND status IN ('pending','withdrawn') RETURNING id''',
            feedback_id,user[0].user_id,payload.content)
    if result is None:
        raise HTTPException(403, 'feedback cannot be edited')
    return {'saved': True}


@app.get('/feedback/{feedback_id}')
async def get_feedback(feedback_id: str,user=Depends(current_user)):
    actor=user[0]
    async with app.state.db.acquire() as conn:
        row=await conn.fetchrow('''SELECT f.*,p.display_name AS author_name,a.title AS announcement_title
            FROM shoe_workbench.feedback f LEFT JOIN shoe_workbench.user_profiles p ON p.user_id=f.author_id
            LEFT JOIN shoe_workbench.announcements a ON a.id=f.announcement_id
            WHERE f.id=$1 AND f.deleted_at IS NULL AND (f.status<>'withdrawn' OR f.author_id=$2 OR $3)''',feedback_id,actor.user_id,actor.is_admin)
    if not row:
        raise HTTPException(404,'feedback not found')
    item=dict(row)
    async with app.state.db.acquire() as conn:
        item=await collaboration.decorate(conn,item,actor,feedback_id,item['anonymous_to_users'])
    return item


@app.delete('/feedback/{feedback_id}')
async def delete_feedback(feedback_id: str, user=Depends(current_user)):
    async with app.state.db.acquire() as conn:
        result = await conn.fetchval('''UPDATE shoe_workbench.feedback SET deleted_at=now(),updated_at=now()
            WHERE id=$1 AND author_id=$2 AND deleted_at IS NULL AND status IN ('pending','withdrawn') RETURNING id''',
            feedback_id,user[0].user_id)
    if result is None:
        raise HTTPException(403, 'feedback cannot be deleted')
    async with app.state.db.acquire() as conn:
        await conn.execute('UPDATE shoe_workbench.announcements SET deleted_at=now() WHERE feedback_id=$1 AND deleted_at IS NULL',feedback_id)
    return {'deleted': True}


@app.get('/feedback/{feedback_id}/messages')
async def feedback_messages(feedback_id: str, user=Depends(current_user)):
    actor = user[0]
    async with app.state.db.acquire() as conn:
        item = await conn.fetchrow('SELECT author_id,status,anonymous_to_users FROM shoe_workbench.feedback WHERE id=$1 AND deleted_at IS NULL', feedback_id)
        if item is None or (item['status'] == 'withdrawn' and not actor.is_admin and item['author_id'] != actor.user_id):
            raise HTTPException(404, 'feedback not found')
        rows = await conn.fetch('''SELECT m.id,m.author_id,m.content,m.created_at,m.reply_to,m.status,m.updated_at,m.edited_at,m.template_reference,
            max(p.display_name) AS author_name,m.anonymous_to_users,
            COALESCE(json_agg(a.id) FILTER (WHERE a.id IS NOT NULL), '[]'::json) AS attachments,
            COALESCE(json_object_agg(a.id,a.content_type) FILTER (WHERE a.id IS NOT NULL), '{}'::json) AS attachment_types
            FROM shoe_workbench.feedback_messages m LEFT JOIN shoe_workbench.attachments a ON a.feedback_message_id=m.id
            LEFT JOIN shoe_workbench.user_profiles p ON p.user_id=m.author_id
            WHERE m.feedback_id=$1 GROUP BY m.id ORDER BY m.created_at,m.id''', feedback_id)
    result = []
    async with app.state.db.acquire() as conn:
        aliases=await collaboration.anonymous_map(conn,feedback_id)
        profiles={}
        for row in rows:
            entry = dict(row)
            entry['attachments'] = json_value(entry['attachments'])
            entry['attachment_types'] = json_value(entry['attachment_types'])
            entry['template_reference'] = json_value(entry['template_reference']) if entry['template_reference'] else None
            entry=await collaboration.decorate(conn,entry,actor,feedback_id,entry['anonymous_to_users'] or entry['author_id'] in aliases,aliases=aliases,profiles=profiles)
            if entry['status']=='deleted' or (entry['status']=='withdrawn' and not actor.is_admin and not entry['is_owner']):
                entry['content']='';entry['attachments']=[];entry['attachment_types']={};entry['template_reference']=None
            result.append(entry)
    return result


async def mutate_feedback_message(feedback_id,message_id,action,payload,user):
    actor=user[0]
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            root=await conn.fetchrow('SELECT author_id,status FROM shoe_workbench.feedback WHERE id=$1 AND deleted_at IS NULL FOR UPDATE',feedback_id)
            if not root or (root['status']=='withdrawn' and not actor.is_admin and root['author_id']!=actor.user_id):raise HTTPException(404,'feedback not found')
            if root['status']=='withdrawn':raise HTTPException(409,'请先恢复这条反馈，再修改回复')
            message=await conn.fetchrow('SELECT * FROM shoe_workbench.feedback_messages WHERE id=$1 AND feedback_id=$2 FOR UPDATE',message_id,feedback_id)
            if not message or message['status']=='deleted':raise HTTPException(404,'reply not found')
            if message['author_id']!=actor.user_id:raise HTTPException(403,'只能修改本人发送的回复')
            if action=='edit':
                if not payload.content.strip():raise HTTPException(422,'请填写回复内容')
                await conn.execute('UPDATE shoe_workbench.feedback_messages SET content=$2,edited_at=now(),updated_at=now() WHERE id=$1',message_id,payload.content)
                notices=await conn.fetch('SELECT id,title FROM shoe_workbench.announcements WHERE feedback_message_id=$1 AND deleted_at IS NULL',message_id)
                for notice in notices:
                    context=await collaboration.notification_context(conn,feedback_id,notice['title'],message_id)
                    await conn.execute('UPDATE shoe_workbench.announcements SET title=$2,content=$3 WHERE id=$1',notice['id'],context[0],context[1])
            else:
                target={'withdraw':'withdrawn','restore':'active','delete':'deleted'}[action]
                if action=='restore' and message['status']!='withdrawn':raise HTTPException(409,'只有已撤回的回复可以恢复')
                await conn.execute('UPDATE shoe_workbench.feedback_messages SET status=$2,updated_at=now() WHERE id=$1',message_id,target)
                if action in ('withdraw','delete'):
                    await conn.execute('UPDATE shoe_workbench.announcements SET deleted_at=now() WHERE feedback_message_id=$1 AND deleted_at IS NULL',message_id)
                if action=='restore':await collaboration.notify_admins(conn,app.state.accounts,feedback_id,actor,'回复已恢复',message_id)
            await conn.execute('UPDATE shoe_workbench.feedback SET updated_at=now() WHERE id=$1',feedback_id)
    return {'saved':True,'id':message_id}


@app.patch('/feedback/{feedback_id}/messages/{message_id}')
async def edit_feedback_message(feedback_id:str,message_id:str,payload:FeedbackEdit,user=Depends(current_user)):
    return await mutate_feedback_message(feedback_id,message_id,'edit',payload,user)


@app.post('/feedback/{feedback_id}/messages/{message_id}/withdraw')
async def withdraw_feedback_message(feedback_id:str,message_id:str,user=Depends(current_user)):
    return await mutate_feedback_message(feedback_id,message_id,'withdraw',None,user)


@app.post('/feedback/{feedback_id}/messages/{message_id}/restore')
async def restore_feedback_message(feedback_id:str,message_id:str,user=Depends(current_user)):
    return await mutate_feedback_message(feedback_id,message_id,'restore',None,user)


@app.delete('/feedback/{feedback_id}/messages/{message_id}')
async def delete_feedback_message(feedback_id:str,message_id:str,user=Depends(current_user)):
    return await mutate_feedback_message(feedback_id,message_id,'delete',None,user)


@app.post('/feedback/{feedback_id}/messages', status_code=201)
async def add_feedback_message(feedback_id: str, payload: FeedbackMessage, user=Depends(current_user)):
    actor = user[0]
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow('SELECT author_id,status FROM shoe_workbench.feedback WHERE id=$1 AND deleted_at IS NULL FOR UPDATE', feedback_id)
            if item is None:
                raise HTTPException(404, 'feedback not found')
            if item['status'] == 'withdrawn':
                raise HTTPException(409, 'feedback is withdrawn')
            if payload.reply_to and not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM shoe_workbench.feedback_messages WHERE id=$1 AND feedback_id=$2 AND status='active')",payload.reply_to,feedback_id):
                raise HTTPException(422, 'reply target is not in this feedback')
            message_id = str(uuid.uuid4())
            anonymous=payload.anonymous_to_users or actor.user_id in await collaboration.anonymous_map(conn,feedback_id)
            reference=None
            if payload.template_id:
                template=await conn.fetchrow('''SELECT t.id,t.title,t.published_version,COALESCE(v.display_name,'v'||v.version::text) AS version_name FROM shoe_workbench.templates t
                    JOIN shoe_workbench.template_versions v ON v.template_id=t.id AND v.version=t.published_version
                    WHERE t.id=$1 AND t.archived_at IS NULL AND v.deleted_at IS NULL''',payload.template_id)
                if not template: raise HTTPException(422,'引用的模板已删除或尚未发布，请重新选择')
                reference=json.dumps({'id':template['id'],'title':template['title'],'version':template['published_version'],'version_name':template['version_name']})
            await conn.execute('INSERT INTO shoe_workbench.feedback_messages(id,feedback_id,author_id,content,reply_to,anonymous_to_users,template_reference) VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)',
                               message_id, feedback_id, actor.user_id, payload.content, payload.reply_to,anonymous,reference)
            await conn.execute('UPDATE shoe_workbench.feedback SET updated_at=now() WHERE id=$1', feedback_id)
            await collaboration.notify_admins(conn,app.state.accounts,feedback_id,actor,'需求反馈有新的回复')
    return {'id': message_id}


@app.post('/feedback/{feedback_id}/notify-author')
async def notify_feedback_author(feedback_id: str, user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        item = await conn.fetchrow("SELECT author_id FROM shoe_workbench.feedback WHERE id=$1 AND deleted_at IS NULL AND status <> 'withdrawn'", feedback_id)
        if not item:
            raise HTTPException(404, 'feedback not found')
        notice_id = 'feedback-notice-' + str(uuid.uuid4())
        context=await collaboration.notification_context(conn,feedback_id,'反馈有新的处理进展')
        await conn.execute('''INSERT INTO shoe_workbench.announcements(id,title,content,audience_user_id,status,created_by,feedback_id,feedback_message_id)
            VALUES($1,$2,$3,$4,'published',$5,$6,$7)''', notice_id,context[0],context[1],item['author_id'],user[0].user_id,feedback_id,context[2])
    return {'id': notice_id}


@app.get('/announcements')
async def announcements(user=Depends(current_user)):
    actor = user[0]
    async with app.state.db.acquire() as conn:
        rows = await conn.fetch('''SELECT a.id,a.title,a.content,a.audience_user_id,a.status,a.created_at,a.updated_at,a.images,a.feedback_id,a.feedback_message_id,a.help_article_id,
            (SELECT COALESCE(json_object_agg(x.id,x.content_type),'{}'::json) FROM shoe_workbench.attachments x WHERE a.images ? x.id) AS media_types,
            r.read_method,r.read_at,r.hidden_at FROM shoe_workbench.announcements a
            LEFT JOIN shoe_workbench.announcement_reads r ON r.announcement_id=a.id AND r.user_id=$1
            WHERE a.deleted_at IS NULL AND (a.feedback_id IS NULL OR EXISTS(SELECT 1 FROM shoe_workbench.feedback f WHERE f.id=a.feedback_id AND f.deleted_at IS NULL)) AND ($2 OR (a.status='published' AND (a.audience_user_id IS NULL OR a.audience_user_id=$1)))
            ORDER BY a.updated_at DESC LIMIT 500''', actor.user_id, actor.is_admin)
    return [dict(row, images=json_value(row['images']),media_types=json_value(row['media_types'])) for row in rows]


@app.put('/admin/announcements/{announcement_id}')
async def save_announcement(announcement_id: str, payload: AnnouncementInput, user=Depends(current_user)):
    require_admin(user)
    if payload.status not in ('draft', 'published', 'withdrawn') or len(announcement_id) > 100:
        raise HTTPException(422, 'invalid announcement')
    async with app.state.db.acquire() as conn:
        if payload.images is not None:
            count = await conn.fetchval('SELECT count(*) FROM shoe_workbench.attachments WHERE id=ANY($1::text[]) AND feedback_message_id IS NULL', payload.images)
            if count != len(set(payload.images)):
                raise HTTPException(422, 'unknown or duplicate image')
        await conn.execute('''INSERT INTO shoe_workbench.announcements(id,title,content,audience_user_id,status,created_by,images)
          VALUES($1,$2,$3,$4,$5,$6,COALESCE($7::jsonb,'[]'::jsonb)) ON CONFLICT(id) DO UPDATE SET title=excluded.title,content=excluded.content,
          audience_user_id=excluded.audience_user_id,status=excluded.status,images=COALESCE($7::jsonb,shoe_workbench.announcements.images),updated_at=now()''',
          announcement_id,payload.title,payload.content,payload.audience_user_id,payload.status,user[0].user_id,json.dumps(payload.images) if payload.images is not None else None)
    return {'id': announcement_id, 'saved': True}


@app.get('/announcements/{announcement_id}/images/{image_id}')
async def announcement_image(announcement_id: str, image_id: str, user=Depends(current_user)):
    try:
        ident = str(uuid.UUID(image_id))
    except ValueError:
        raise HTTPException(404, 'image not found')
    root_setting = os.environ.get('SHOE_UPLOAD_DIR')
    if not root_setting:
        raise HTTPException(503, 'upload storage is not configured')
    async with app.state.db.acquire() as conn:
        row = await conn.fetchrow('''SELECT f.storage_key,f.content_type FROM shoe_workbench.attachments f
          JOIN shoe_workbench.announcements a ON a.images ? f.id
          WHERE a.id=$1 AND f.id=$2 AND f.feedback_message_id IS NULL AND a.deleted_at IS NULL
          AND ($3 OR (a.status='published' AND (a.audience_user_id IS NULL OR a.audience_user_id=$4)))''',
          announcement_id,ident,user[0].is_admin,user[0].user_id)
    path = Path(root_setting).resolve() / ident
    if not row or row['storage_key'] != ident or not path.is_file():
        raise HTTPException(404, 'image not found')
    return FileResponse(path,media_type=row['content_type'],headers={'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})


@app.delete('/admin/announcements/{announcement_id}')
async def delete_announcement(announcement_id: str, user=Depends(current_user)):
    require_admin(user)
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow('SELECT status FROM shoe_workbench.announcements WHERE id=$1 FOR UPDATE', announcement_id)
            if row is None:
                raise HTTPException(404, 'announcement not found')
            if row['status'] != 'withdrawn':
                raise HTTPException(409, 'withdraw announcement before deleting')
            await conn.execute('UPDATE shoe_workbench.announcements SET deleted_at=now() WHERE id=$1', announcement_id)
    return {'deleted': True}


@app.post('/announcements/{announcement_id}/read')
async def read_announcement(announcement_id: str, payload: ReadInput, user=Depends(current_user)):
    if payload.method not in ('opened', 'marked_read'):
        raise HTTPException(422, 'invalid read method')
    async with app.state.db.acquire() as conn:
        result = await conn.fetchval('''INSERT INTO shoe_workbench.announcement_reads(announcement_id,user_id,read_method)
          SELECT id,$2,$3 FROM shoe_workbench.announcements WHERE id=$1 AND status='published'
          AND (audience_user_id IS NULL OR audience_user_id=$2)
          ON CONFLICT(announcement_id,user_id) DO UPDATE SET
          read_method=CASE WHEN shoe_workbench.announcement_reads.read_method='opened' AND shoe_workbench.announcement_reads.read_at >=
          (SELECT updated_at FROM shoe_workbench.announcements WHERE id=$1) THEN 'opened' ELSE excluded.read_method END,
          read_at=now() RETURNING announcement_id''', announcement_id,user[0].user_id,payload.method)
    if result is None:
        raise HTTPException(404, 'announcement not found')
    return {'read': True}


@app.get('/admin/announcements/{announcement_id}/read-summary')
async def announcement_read_summary(announcement_id: str,user=Depends(current_user)):
    require_admin(user)
    shared=await shared_accounts.directory(app.state.accounts) if app.state.accounts else []
    active_ids=[p['user_id'] for p in shared if p['status']=='active']
    async with app.state.db.acquire() as conn:
        active_ids.extend([r['id'] for r in await conn.fetch('SELECT id FROM shoe_workbench.local_accounts WHERE active')])
        notice=await conn.fetchrow('SELECT audience_user_id FROM shoe_workbench.announcements WHERE id=$1 AND deleted_at IS NULL',announcement_id)
        if not notice:
            raise HTTPException(404,'announcement not found')
        if notice['audience_user_id']:
            active_ids=[x for x in active_ids if x==notice['audience_user_id']]
        counts=await conn.fetchrow('''SELECT count(*) AS read_count,count(*) FILTER(WHERE r.read_method='opened') AS opened,
            count(*) FILTER(WHERE r.read_method='marked_read') AS marked_read FROM shoe_workbench.announcement_reads r
            JOIN shoe_workbench.announcements a ON a.id=r.announcement_id WHERE a.id=$1 AND r.read_at>=a.updated_at AND r.user_id=ANY($2::text[])''',announcement_id,active_ids)
    return dict(counts,audience=len(active_ids))


@app.post('/feedback/{feedback_id}/attachments', status_code=201)
async def upload_feedback_image(feedback_id: str, request: Request, user=Depends(current_user)):
    actor, _ = user
    root_setting = os.environ.get('SHOE_UPLOAD_DIR')
    if not root_setting:
        raise HTTPException(503, 'upload storage is not configured')
    content_type, data = await media_support.read_upload(request)
    ident, message_id = str(uuid.uuid4()), str(uuid.uuid4())
    existing_message = request.headers.get('x-feedback-message-id')
    root = Path(root_setting).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / ident
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow('SELECT author_id,status FROM shoe_workbench.feedback WHERE id=$1 AND deleted_at IS NULL FOR UPDATE', feedback_id)
            if item is None:
                raise HTTPException(404, 'feedback not found')
            if item['status']=='withdrawn':
                raise HTTPException(409,'feedback is withdrawn')
            if existing_message:
                if not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM shoe_workbench.feedback_messages WHERE id=$1 AND feedback_id=$2 AND author_id=$3 AND status='active')",existing_message,feedback_id,actor.user_id):
                    raise HTTPException(422,'invalid message target')
                message_id=existing_message
            try:
                with path.open('xb') as output:
                    output.write(data)
                os.chmod(path, 0o600)
                if not existing_message:
                    await conn.execute('''INSERT INTO shoe_workbench.feedback_messages
                        (id,feedback_id,author_id,content) VALUES($1,$2,$3,'')''', message_id, feedback_id, actor.user_id)
                await conn.execute('''INSERT INTO shoe_workbench.attachments
                    (id,owner_id,feedback_message_id,storage_key,content_type,byte_size)
                    VALUES($1,$2,$3,$4,$5,$6)''', ident, actor.user_id, message_id, ident, content_type, len(data))
            except Exception:
                path.unlink(missing_ok=True)
                raise
    return {'id': ident, 'size': len(data)}


@app.get('/attachments/{attachment_id}')
async def read_feedback_image(attachment_id: str, user=Depends(current_user)):
    try:
        canonical = str(uuid.UUID(attachment_id))
    except ValueError:
        raise HTTPException(404, 'attachment not found')
    root_setting = os.environ.get('SHOE_UPLOAD_DIR')
    if not root_setting:
        raise HTTPException(503, 'upload storage is not configured')
    async with app.state.db.acquire() as conn:
        row = await conn.fetchrow('''SELECT a.storage_key,a.content_type,f.status,f.author_id,m.status AS message_status,m.author_id AS message_author
          FROM shoe_workbench.attachments a
          JOIN shoe_workbench.feedback_messages m ON m.id=a.feedback_message_id
          JOIN shoe_workbench.feedback f ON f.id=m.feedback_id WHERE a.id=$1 AND f.deleted_at IS NULL''', canonical)
    if row is None or (row['status'] == 'withdrawn' and not user[0].is_admin and user[0].user_id != row['author_id']):
        raise HTTPException(404, 'attachment not found')
    if row['message_status']=='deleted' or (row['message_status']=='withdrawn' and not user[0].is_admin and user[0].user_id!=row['message_author']):raise HTTPException(404,'attachment not found')
    if row['storage_key'] != canonical:
        raise HTTPException(404, 'attachment not found')
    path = Path(root_setting).resolve() / canonical
    if not path.is_file():
        raise HTTPException(404, 'attachment not found')
    return FileResponse(path, media_type=row['content_type'], headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})


@app.patch('/feedback/{feedback_id}/status')
async def change_feedback_status(feedback_id: str, payload: FeedbackTransition, user=Depends(current_user)):
    actor, _ = user
    async with app.state.db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow('SELECT author_id,status FROM shoe_workbench.feedback WHERE id=$1 AND deleted_at IS NULL FOR UPDATE', feedback_id)
            if row is None:
                raise HTTPException(404, 'feedback not found')
            if not can_transition_feedback(actor, row['author_id'], row['status'], payload.status):
                raise HTTPException(403, 'transition not allowed')
            await conn.execute('''UPDATE shoe_workbench.feedback SET status=$2,updated_at=now(),
                resolved_at=CASE WHEN $2='resolved' THEN now() ELSE NULL END WHERE id=$1''', feedback_id, payload.status)
            if not actor.is_admin:
                await collaboration.notify_admins(conn,app.state.accounts,feedback_id,actor,'需求反馈状态已更新')
            if actor.is_admin and actor.user_id != row['author_id'] and payload.status == 'awaiting_confirmation':
                context=await collaboration.notification_context(conn,feedback_id,'反馈待确认')
                await conn.execute('''INSERT INTO shoe_workbench.announcements
                  (id,title,content,audience_user_id,status,created_by,feedback_id,feedback_message_id) VALUES($1,$2,$3,$4,'published',$5,$6,$7)''',
                  'feedback-notice-'+str(uuid.uuid4()),context[0],context[1],row['author_id'],actor.user_id,feedback_id,context[2])
    return {'id': feedback_id, 'status': payload.status}


help_center.register(app,current_user,require_admin)
media_support.register(app,current_user)

# Static assets and API are deployed by the same service; file contents are
# public but all application data routes still require a verified identity.
frontend_dir = os.environ.get('SHOE_FRONTEND_DIR')
if frontend_dir:
    app.mount('/shoe', StaticFiles(directory=frontend_dir, html=True), name='shoe-frontend')
