"""Live regression with isolated disposable accounts; never modify employee passwords."""
import asyncio,os,uuid,secrets,sys,base64
from pathlib import Path
sys.path.insert(0,'/home/ubuntu/shoe-workbench-staging/backend')
import asyncpg,httpx
from app import hash_local_password

async def main():
    db=await asyncpg.connect(os.environ['SHOE_DATABASE_URL'])
    tag=secrets.token_hex(6);ids=['shoe-user-'+str(uuid.uuid4()) for _ in range(4)]
    password=secrets.token_urlsafe(24);clients=[];fids=[]
    async def req(client,method,path,body=None):
        r=await client.request(method,path,json=body) if body is not None else await client.request(method,path)
        assert r.status_code<400,(method,path,r.status_code,r.text[:100])
        return r.json()
    async def login(client,index):
        r=await client.post('/auth/login',json={'username':'check_collab_'+tag+str(index),'password':password,'remember':True})
        assert r.status_code==200,(r.status_code,r.text[:100])
        old={p.split('=',1)[0]:p.split('=',1)[1] for p in client.headers.get('Cookie','').split('; ') if '=' in p}
        old.update(dict(r.cookies))
        client.headers['Cookie']='; '.join(k+'='+v for k,v in old.items())
        return await req(client,'GET','/me')
    try:
        for i,role in enumerate(['super_admin','operator','operator','management']):
            await db.execute('INSERT INTO shoe_workbench.local_accounts(id,username,display_name,role_code,category,password_hash) VALUES($1,$2,$3,$4,$5,$6)',ids[i],'check_collab_'+tag+str(i),'验收人员'+str(i),role,'验收',hash_local_password(password))
            c=httpx.AsyncClient(base_url='http://127.0.0.1:18808',timeout=30);clients.append(c);u=await login(c,i)
            assert u['admin']==(i in (0,3))
        admin,one,two,manager=clients
        root=await req(one,'POST','/feedback',{'category':'验收','content':'匿名验证','anonymous_to_users':True});fid=root['id'];fids.append(fid)
        assert (await req(one,'GET','/feedback/'+fid))['author_name']=='匿名1'
        assert (await req(two,'GET','/feedback/'+fid))['author_id'] is None
        assert '验收人员1' in (await req(admin,'GET','/feedback/'+fid))['author_name']
        m=await req(two,'POST','/feedback/'+fid+'/messages',{'content':'其他运营补充','anonymous_to_users':True})
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        image=await two.post('/feedback/'+fid+'/attachments',content=png,headers={'Content-Type':'image/png','X-Feedback-Message-Id':m['id']});assert image.status_code==201
        image_path='/attachments/'+image.json()['id'];assert (await one.get(image_path)).status_code==200
        await req(two,'POST','/feedback/'+fid+'/messages',{'content':'同一匿名身份继续','reply_to':m['id']})
        messages=await req(one,'GET','/feedback/'+fid+'/messages')
        assert [r['author_name'] for r in messages]==['匿名2','匿名2']
        assert all(r['author_id'] is None for r in messages)
        assert (await one.patch('/feedback/'+fid+'/messages/'+m['id'],json={'content':'不允许代改'})).status_code==403
        assert (await admin.delete('/feedback/'+fid+'/messages/'+m['id'])).status_code==403
        await req(two,'PATCH','/feedback/'+fid+'/messages/'+m['id'],{'content':'编辑后的补充'})
        assert any(r['id']==m['id'] and r['edited_at'] for r in await req(one,'GET','/feedback/'+fid+'/messages'))
        child=await req(one,'POST','/feedback/'+fid+'/messages',{'content':'保留后续讨论','reply_to':m['id']})
        await req(two,'POST','/feedback/'+fid+'/messages/'+m['id']+'/withdraw')
        assert (await one.get(image_path)).status_code==404
        assert (await two.get(image_path)).status_code==200
        assert (await admin.get(image_path)).status_code==200
        visible=await req(one,'GET','/feedback/'+fid+'/messages')
        assert next(r for r in visible if r['id']==m['id'])['content']==''
        assert any(r['id']==child['id'] and r['content']=='保留后续讨论' for r in visible)
        assert next(r for r in await req(two,'GET','/feedback/'+fid+'/messages') if r['id']==m['id'])['content']=='编辑后的补充'
        assert next(r for r in await req(admin,'GET','/feedback/'+fid+'/messages') if r['id']==m['id'])['content']=='编辑后的补充'
        assert not any(n.get('feedback_message_id')==m['id'] for n in await req(admin,'GET','/announcements'))
        await req(two,'POST','/feedback/'+fid+'/messages/'+m['id']+'/restore')
        assert (await one.get(image_path)).status_code==200
        assert next(r for r in await req(one,'GET','/feedback/'+fid+'/messages') if r['id']==m['id'])['content']=='编辑后的补充'
        await req(two,'DELETE','/feedback/'+fid+'/messages/'+m['id'])
        assert (await two.get(image_path)).status_code==404
        assert (await admin.get(image_path)).status_code==404
        assert next(r for r in await req(admin,'GET','/feedback/'+fid+'/messages') if r['id']==m['id'])['content']==''
        assert (await one.post('/feedback/'+fid+'/messages',json={'content':'引用已删除回复','reply_to':m['id']})).status_code==422
        await req(one,'PATCH','/feedback/'+fid+'/status',{'status':'withdrawn'})
        assert (await two.get('/feedback/'+fid)).status_code==404
        assert (await two.get('/feedback/'+fid+'/messages')).status_code==404
        assert not any(r['id']==fid for r in await req(two,'GET','/feedback'))
        assert (await req(one,'GET','/feedback/'+fid))['status']=='withdrawn'
        assert (await req(admin,'GET','/feedback/'+fid))['status']=='withdrawn'
        await req(one,'PATCH','/feedback/'+fid+'/status',{'status':'pending'})
        root2=await req(one,'POST','/feedback',{'category':'验收','content':'实名验证'});fid2=root2['id'];fids.append(fid2)
        await req(two,'POST','/feedback/'+fid2+'/messages',{'content':'新问题重新编号','anonymous_to_users':True})
        assert (await req(one,'GET','/feedback/'+fid2+'/messages'))[0]['author_name']=='匿名1'
        assert '验收人员1' in (await req(two,'GET','/feedback/'+fid2))['author_name']
        await req(manager,'PATCH','/feedback/'+fid+'/status',{'status':'resolved'})
        await req(two,'PATCH','/feedback/'+fid+'/status',{'status':'processing'})
        notices=await req(admin,'GET','/announcements')
        assert sum(n.get('feedback_id')==fid for n in notices)>=4
        assert any(n.get('feedback_id')==fid for n in await req(manager,'GET','/announcements'))
        reply_notice=next(n for n in notices if n.get('feedback_message_id')==child['id'] and n['title']=='反馈有新回复')
        assert '保留后续讨论' in reply_notice['content'] and '匿名验证' in reply_notice['content']
        assert reply_notice['title']=='反馈有新回复' and '回复人：匿名1' in reply_notice['content']
        await req(one,'DELETE','/feedback/'+fid2)
        assert not any(n.get('feedback_id')==fid2 for n in await req(admin,'GET','/announcements'))
        await db.execute("UPDATE shoe_workbench.local_accounts SET role_code='operator' WHERE id=$1",ids[3])
        assert not (await req(manager,'GET','/me'))['admin']
        assert (await manager.get('/admin/templates')).status_code==403
        await db.execute("UPDATE shoe_workbench.local_accounts SET role_code='management' WHERE id=$1",ids[3])
        assert (await req(manager,'GET','/me'))['admin']
        print('PASS: per-thread anonymity, cross-user replies, role changes, admin inbox, notification snippets and target message, deleted feedback notification cleanup')
        print('PASS: owner-only reply edit/withdraw/restore/delete, private withdrawn text, quote tombstones, preserved child discussion, withdrawn-feedback visibility')
        await login(one,0)
        records=await req(one,'GET','/auth/accounts');assert {r['account_id'] for r in records}=={ids[0],ids[1]}
        switched=await one.post('/auth/accounts/'+ids[1]+'/switch');assert switched.status_code==200
        cookie=dict(switched.cookies);device=one.headers['Cookie'].split('shoe_device=',1)[1].split(';')[0]
        one.headers['Cookie']='shoe_device='+device+'; shoe_session='+cookie['shoe_session']
        assert not (await req(one,'GET','/me'))['admin']
        assert (await two.post('/auth/accounts/'+ids[0]+'/switch')).status_code==404
        await req(one,'DELETE','/auth/accounts');assert len(await req(one,'GET','/auth/accounts'))==1
        await login(one,0)
        await db.execute('DELETE FROM shoe_workbench.local_sessions WHERE user_id=$1',ids[1])
        assert (await one.post('/auth/accounts/'+ids[1]+'/switch')).status_code==401
        print('PASS: saved account click-switch, real role changes, device isolation, clear others, invalidated session rejected')
    finally:
        for c in clients: await c.aclose()
        for fid in fids:
            attachments=await db.fetch('SELECT storage_key FROM shoe_workbench.attachments WHERE feedback_message_id IN (SELECT id FROM shoe_workbench.feedback_messages WHERE feedback_id=$1)',fid)
            await db.execute('DELETE FROM shoe_workbench.attachments WHERE feedback_message_id IN (SELECT id FROM shoe_workbench.feedback_messages WHERE feedback_id=$1)',fid)
            for attachment in attachments:
                key=str(uuid.UUID(attachment['storage_key']))
                (Path(os.environ.get('SHOE_UPLOAD_DIR','/home/ubuntu/shoe-workbench-staging/uploads')).resolve()/key).unlink(missing_ok=True)
            await db.execute('DELETE FROM shoe_workbench.feedback_messages WHERE feedback_id=$1',fid)
            await db.execute('DELETE FROM shoe_workbench.feedback WHERE id=$1',fid)
        await db.execute("DELETE FROM shoe_workbench.announcements WHERE created_by=ANY($1::text[]) OR audience_user_id=ANY($1::text[])",ids)
        await db.execute('DELETE FROM shoe_workbench.remembered_accounts WHERE account_id=ANY($1::text[])',ids)
        for uid in ids:
            await db.execute('DELETE FROM shoe_workbench.local_sessions WHERE user_id=$1',uid)
            await db.execute('DELETE FROM shoe_workbench.local_accounts WHERE id=$1',uid)
            await db.execute('DELETE FROM shoe_workbench.user_profiles WHERE user_id=$1',uid)
        await db.close()

asyncio.run(main())
