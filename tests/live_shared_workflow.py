"""Verify real shared-account administration and threaded image delivery, then remove owned fixtures."""
import asyncio
import base64
import os
import secrets
import uuid
from pathlib import Path

import asyncpg
import httpx
from app import hash_local_password


async def main():
    db=await asyncpg.connect(os.environ['SHOE_DATABASE_URL'])
    identity=await asyncpg.connect(os.environ['SHOE_IDENTITY_DATABASE_URL'])
    admin_id='shoe-user-'+str(uuid.uuid4())
    admin_name='shoe_check_'+secrets.token_hex(8)
    admin_password=secrets.token_urlsafe(24)
    uid=None;fid=None;image_ids=[];notice_ids=[]
    username='art_check_'+secrets.token_hex(6)
    async def ok(response):
        if response.status_code>=400:
            raise AssertionError(f'{response.request.method} {response.request.url.path}: HTTP {response.status_code}, {response.text[:200]}')
        return response.json()
    try:
        await db.execute('INSERT INTO shoe_workbench.local_accounts(id,username,display_name,role_code,category,password_hash) VALUES($1,$2,$3,$4,$5,$6)',admin_id,admin_name,'自动验收管理员','super_admin','验收',hash_local_password(admin_password))
        async with httpx.AsyncClient(base_url='http://127.0.0.1:18808',timeout=20) as admin, httpx.AsyncClient(base_url='http://127.0.0.1:18808',timeout=20) as person, httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=20) as cpo:
            login=await admin.post('/auth/login',json={'username':admin_name,'password':admin_password});await ok(login)
            admin.headers['Authorization']='Bearer '+login.cookies['shoe_session']
            people=await ok(await admin.get('/admin/users/activity'))
            assert sum(p['source']=='shared' for p in people)>=20
            created=await ok(await admin.post('/admin/accounts',json={'username':username,'display_name':'验收临时人员','category':'美工','role':'workbench_user'}))
            uid=created['user_id'];password=created['initial_password']
            cpo_login=await ok(await cpo.post('/api/auth/login',json={'username':username,'password':password}))
            assert str(cpo_login['user']['userId'])==uid
            p_login=await person.post('/auth/login',json={'username':username,'password':password});await ok(p_login)
            person.headers['Authorization']='Bearer '+p_login.cookies['shoe_session']
            await ok(await person.get('/me'))
            changed='prod_'+username.removeprefix('art_')
            values={'username':changed,'display_name':'验收临时人员','category':'产品','role':'workbench_user','active':True}
            await ok(await admin.patch('/admin/accounts/'+uid,json=values))
            assert (await cpo.post('/api/auth/login',json={'username':username,'password':password})).status_code==401
            await ok(await cpo.post('/api/auth/login',json={'username':changed,'password':password}))
            people=await ok(await admin.get('/admin/users/activity'))
            assert any(p['user_id']==uid and p['username']==changed and p['category']=='产品' for p in people)
            values['active']=False;await ok(await admin.patch('/admin/accounts/'+uid,json=values))
            assert (await person.get('/me')).status_code==401
            assert (await cpo.post('/api/auth/login',json={'username':changed,'password':password})).status_code==401
            values['active']=True;await ok(await admin.patch('/admin/accounts/'+uid,json=values))
            await ok(await person.post('/auth/reset-requests',json={'identifier':changed,'note':'自动验收申请'}))
            requests=await ok(await admin.get('/admin/reset-requests'))
            assert any(r['identifier']==changed for r in requests)
            reset=await ok(await admin.post('/admin/accounts/'+uid+'/reset-password'));password=reset['initial_password']
            requests=await ok(await admin.get('/admin/reset-requests'))
            assert not any(r['identifier']==changed for r in requests)
            await ok(await cpo.post('/api/auth/login',json={'username':changed,'password':password}))
            p_login=await person.post('/auth/login',json={'username':changed,'password':password});await ok(p_login)
            person.headers['Authorization']='Bearer '+p_login.cookies['shoe_session']
            feedback=await ok(await person.post('/feedback',json={'category':'自动验收','content':'自动验收临时反馈'}));fid=feedback['id']
            parent=await ok(await person.post('/feedback/'+fid+'/messages',json={'content':'原消息'}))
            reply=await ok(await admin.post('/feedback/'+fid+'/messages',json={'content':'引用回复','reply_to':parent['id']}))
            png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jSPkAAAAASUVORK5CYII=')
            image=await ok(await admin.post('/feedback/'+fid+'/attachments',content=png,headers={'Content-Type':'image/png','X-Feedback-Message-Id':reply['id']}));image_ids.append(image['id'])
            messages=await ok(await person.get('/feedback/'+fid+'/messages'))
            assert len(messages)==2
            assert any(m['id']==reply['id'] and m['reply_to']==parent['id'] and image['id'] in m['attachments'] for m in messages)
            notice=await ok(await admin.post('/feedback/'+fid+'/notify-author'));notice_ids.append(notice['id'])
            notices=await ok(await person.get('/announcements'))
            assert any(n['id']==notice['id'] and str(n['audience_user_id'])==uid and n['read_at'] is None for n in notices)
            await ok(await admin.patch('/feedback/'+fid+'/status',json={'status':'resolved'}))
            await ok(await person.patch('/feedback/'+fid+'/status',json={'status':'processing'}))
            await ok(await admin.delete('/admin/accounts/'+uid))
            assert (await cpo.post('/api/auth/login',json={'username':changed,'password':password})).status_code==401
            uid=None
            print('PASS: existing shared directory, create, rename/category, disable, restore, reset and delete; CPO login uses the same changes')
            print('PASS: threaded text plus image share one message, private unread notice, resolve, reopen and password request lifecycle')
    finally:
        if uid:
            await identity.execute('DELETE FROM iam.users WHERE user_id=$1',int(uid))
        for nid in notice_ids:
            await db.execute('DELETE FROM shoe_workbench.announcement_reads WHERE announcement_id=$1',nid)
            await db.execute('DELETE FROM shoe_workbench.announcements WHERE id=$1',nid)
        if fid:
            await db.execute('DELETE FROM shoe_workbench.attachments WHERE feedback_message_id IN (SELECT id FROM shoe_workbench.feedback_messages WHERE feedback_id=$1)',fid)
            await db.execute('DELETE FROM shoe_workbench.feedback_messages WHERE feedback_id=$1',fid)
            await db.execute('DELETE FROM shoe_workbench.feedback WHERE id=$1',fid)
        for image_id in image_ids:
            (Path(os.environ.get('SHOE_UPLOAD_DIR','/home/ubuntu/shoe-workbench-staging/uploads'))/image_id).unlink(missing_ok=True)
        await db.execute('DELETE FROM shoe_workbench.local_sessions WHERE user_id=$1',admin_id)
        await db.execute('DELETE FROM shoe_workbench.user_profiles WHERE user_id=$1 OR username=$2 OR username=$3',admin_id,username,'prod_'+username.removeprefix('art_'))
        await db.execute('DELETE FROM shoe_workbench.account_audit WHERE actor_id=$1',admin_id)
        await db.execute('DELETE FROM shoe_workbench.password_requests WHERE identifier=$1 OR identifier=$2',username,'prod_'+username.removeprefix('art_'))
        await db.execute('DELETE FROM shoe_workbench.local_accounts WHERE id=$1',admin_id)
        await identity.close();await db.close()

asyncio.run(main())
