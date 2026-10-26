"""Remove only interrupted disposable collaboration test fixtures."""
import asyncio,os,asyncpg,base64,uuid,time
from pathlib import Path
async def main():
    conn=await asyncpg.connect(os.environ['SHOE_DATABASE_URL'])
    try:
        users=await conn.fetch("SELECT id,username FROM shoe_workbench.local_accounts WHERE username LIKE 'check_collab_%' AND display_name LIKE '验收人员%' AND category='验收'")
        ids=[u['id'] for u in users]
        fids=[r['id'] for r in await conn.fetch('SELECT id FROM shoe_workbench.feedback WHERE author_id=ANY($1::text[])',ids)]
        for fid in fids:
            await conn.execute('DELETE FROM shoe_workbench.attachments WHERE feedback_message_id IN (SELECT id FROM shoe_workbench.feedback_messages WHERE feedback_id=$1)',fid)
            await conn.execute('DELETE FROM shoe_workbench.feedback_messages WHERE feedback_id=$1',fid)
            await conn.execute('DELETE FROM shoe_workbench.feedback WHERE id=$1',fid)
        await conn.execute('DELETE FROM shoe_workbench.announcements WHERE created_by=ANY($1::text[]) OR audience_user_id=ANY($1::text[])',ids)
        await conn.execute('DELETE FROM shoe_workbench.remembered_accounts WHERE account_id=ANY($1::text[])',ids)
        for u in users:
            await conn.execute('DELETE FROM shoe_workbench.local_sessions WHERE user_id=$1',u['id'])
            await conn.execute('DELETE FROM shoe_workbench.user_profiles WHERE user_id=$1',u['id'])
            await conn.execute('DELETE FROM shoe_workbench.login_attempts WHERE key=$1',u['username'])
            await conn.execute('DELETE FROM shoe_workbench.local_accounts WHERE id=$1',u['id'])
        root=Path('/home/ubuntu/shoe-workbench-staging/uploads').resolve()
        fixture=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        removed=0
        for p in root.iterdir():
            if not p.is_file() or p.stat().st_size!=len(fixture) or time.time()-p.stat().st_mtime>1800:continue
            try:uuid.UUID(p.name)
            except ValueError:continue
            if p.read_bytes()!=fixture:continue
            if not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM shoe_workbench.attachments WHERE storage_key=$1)',p.name):p.unlink();removed+=1
        print('Temporary accounts cleaned:',len(users),'Unreferenced exact test PNG files cleaned:',removed)
    finally:await conn.close()
asyncio.run(main())
