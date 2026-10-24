"""Verify linked announcement feedback, versioned short-value metrics and personal suggestions."""
import asyncio
import json
import os
import secrets
import uuid
import asyncpg
import httpx
from app import hash_local_password

async def main():
    db=await asyncpg.connect(os.environ['SHOE_DATABASE_URL'])
    suffix=secrets.token_hex(8);uids=['shoe-user-'+str(uuid.uuid4()) for _ in range(2)]
    tid='verify-template-'+suffix;nid='verify-notice-'+suffix;fid=None
    password=secrets.token_urlsafe(24)
    async def ok(response):
        if response.status_code>=400:
            raise AssertionError(f'{response.request.method} {response.request.url.path}: {response.status_code} {response.text[:200]}')
        return response.json()
    try:
        for index,uid in enumerate(uids):
            await db.execute('INSERT INTO shoe_workbench.local_accounts(id,username,display_name,role_code,category,password_hash) VALUES($1,$2,$3,$4,$5,$6)',uid,'shoe_check_'+suffix+str(index),'临时功能验收','super_admin' if index==0 else 'operator','验收',hash_local_password(password))
        async with httpx.AsyncClient(base_url='http://127.0.0.1:18808',timeout=15) as admin,httpx.AsyncClient(base_url='http://127.0.0.1:18808',timeout=15) as person:
            for index,client in enumerate([admin,person]):
                login=await client.post('/auth/login',json={'username':'shoe_check_'+suffix+str(index),'password':password});await ok(login)
                client.headers['Authorization']='Bearer '+login.cookies['shoe_session'];await ok(await client.get('/me'))
            content={'body':'【场景】','bracketFields':[{'index':0,'mode':'replace','label':'场景','control':'text','options':[]}]}
            for version in (1,2):
                await ok(await admin.put('/admin/templates/'+tid+'/draft',json={'title':'临时统计验收模板','media_type':'image','content':content}))
                await ok(await admin.post('/admin/templates/'+tid+'/publish'))
                for _ in range(3 if version==1 else 1):
                    await ok(await person.post('/events',json={'event_type':'prompt_copy','template_id':tid,'template_version':version,'field_values':[{'key':'0','value':'草坪运动'}]}))
            suggested=await ok(await person.get('/templates/'+tid+'/suggestions?version=1'))
            assert any(r['field_key']=='0' and r['value']=='草坪运动' and r['use_count']==3 for r in suggested)
            assert await ok(await person.get('/templates/'+tid+'/suggestions?version=2'))==[]
            assert await ok(await admin.get('/templates/'+tid+'/suggestions?version=1'))==[]
            dashboard=await ok(await admin.get('/admin/usage-dashboard?days=30'))
            rows=[r for r in dashboard['rows'] if r['template_id']==tid]
            assert {r['template_version']:r['copies'] for r in rows}=={1:3,2:1}
            assert any(r['template_id']==tid and r['value']=='草坪运动' and r['uses']==3 for r in dashboard['words'])
            assert (await person.get('/admin/usage-dashboard')).status_code==403
            await ok(await admin.put('/admin/announcements/'+nid,json={'title':'临时验收公告','content':'公告正文','status':'published'}))
            await ok(await person.post('/announcements/'+nid+'/read',json={'method':'opened'}))
            await ok(await person.post('/announcements/'+nid+'/read',json={'method':'marked_read'}))
            stats=await ok(await admin.get('/admin/announcements/'+nid+'/read-summary'))
            assert stats['opened']==1 and stats['marked_read']==0 and stats['read_count']==1
            feedback=await ok(await person.post('/feedback',json={'category':'公告疑问','content':'针对这条公告的问题','announcement_id':nid}));fid=feedback['id']
            listed=await ok(await admin.get('/feedback'))
            assert any(r['id']==fid and r['announcement_id']==nid and r['announcement_title']=='临时验收公告' for r in listed)
            await ok(await admin.put('/admin/announcements/'+nid,json={'title':'临时验收公告','content':'公告正文','status':'withdrawn'}))
            await ok(await admin.delete('/admin/announcements/'+nid))
            listed=await ok(await admin.get('/feedback'))
            assert any(r['id']==fid and r['announcement_id']==nid for r in listed)
            assert not any(r['id']==nid for r in await ok(await admin.get('/announcements')))
            submission_id=str(uuid.uuid4())
            payload={'category':'功能核验','content':'提交后删除，再次提交','submission_id':submission_id}
            first=await ok(await person.post('/feedback',json=payload))
            duplicate=await ok(await person.post('/feedback',json=payload))
            assert first['id']==duplicate['id']==submission_id
            await ok(await person.delete('/feedback/'+submission_id))
            assert not any(r['id']==submission_id for r in await ok(await person.get('/feedback')))
            await db.execute('DELETE FROM shoe_workbench.feedback WHERE id=$1',submission_id)
            payload['submission_id']=str(uuid.uuid4())
            again=await ok(await person.post('/feedback',json=payload))
            assert any(r['id']==again['id'] for r in await ok(await person.get('/feedback')))
            await ok(await person.delete('/feedback/'+again['id']))
            await db.execute('DELETE FROM shoe_workbench.feedback WHERE id=$1',again['id'])
            print('PASS: feedback submit retry is idempotent; delete and resubmit works')
            print('PASS: per-user/per-version short-value counts, three-copy suggestions, dashboard and admin authorization')
            print('PASS: feedback links to announcement; removing announcement preserves feedback provenance')
    finally:
        if fid:await db.execute('DELETE FROM shoe_workbench.feedback WHERE id=$1',fid)
        await db.execute('DELETE FROM shoe_workbench.announcement_reads WHERE announcement_id=$1',nid)
        await db.execute('DELETE FROM shoe_workbench.announcements WHERE id=$1',nid)
        await db.execute('DELETE FROM shoe_workbench.field_usage WHERE template_id=$1',tid)
        await db.execute('DELETE FROM shoe_workbench.events WHERE template_id=$1',tid)
        await db.execute('DELETE FROM shoe_workbench.template_versions WHERE template_id=$1',tid)
        await db.execute('DELETE FROM shoe_workbench.templates WHERE id=$1',tid)
        await db.execute('DELETE FROM shoe_workbench.local_sessions WHERE user_id=ANY($1::text[])',uids)
        await db.execute('DELETE FROM shoe_workbench.user_profiles WHERE user_id=ANY($1::text[])',uids)
        await db.execute('DELETE FROM shoe_workbench.local_accounts WHERE id=ANY($1::text[])',uids)
        await db.close()

asyncio.run(main())
