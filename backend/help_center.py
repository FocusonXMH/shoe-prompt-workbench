"""Published learning articles, per-person onboarding, and announcement links."""
import json
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

class ArticleInput(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    content: str = Field(default='', max_length=20000)
    images: list[str] = Field(default_factory=list, max_length=50)
    status: str = 'draft'

def register(app, current_user, require_admin):
    @app.get('/help/articles')
    async def articles(user=Depends(current_user)):
        actor=user[0]
        async with app.state.db.acquire() as conn:
            rows=await conn.fetch('''SELECT a.*,r.read_at FROM shoe_workbench.help_articles a
                LEFT JOIN shoe_workbench.help_reads r ON r.article_id=a.id AND r.user_id=$1
                WHERE $2 OR a.status='published' ORDER BY a.created_at,a.id''',actor.user_id,actor.is_admin)
            seen=await conn.fetchval('SELECT EXISTS(SELECT 1 FROM shoe_workbench.help_onboarding WHERE user_id=$1)',actor.user_id)
        return {'articles':[dict(r,images=json.loads(r['images']) if isinstance(r['images'],str) else r['images']) for r in rows], 'onboarding_seen':seen}

    @app.post('/help/onboarding/seen')
    async def acknowledge(user=Depends(current_user)):
        async with app.state.db.acquire() as conn:
            await conn.execute('INSERT INTO shoe_workbench.help_onboarding(user_id) VALUES($1) ON CONFLICT DO NOTHING',user[0].user_id)
        return {'seen':True}

    @app.post('/help/articles/{article_id}/read')
    async def read(article_id:str,user=Depends(current_user)):
        actor=user[0]
        async with app.state.db.acquire() as conn:
            async with conn.transaction():
                if not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM shoe_workbench.help_articles WHERE id=$1 AND ($2 OR status='published'))",article_id,actor.is_admin):raise HTTPException(404,'说明不存在或已撤回')
                await conn.execute('INSERT INTO shoe_workbench.help_reads(article_id,user_id) VALUES($1,$2) ON CONFLICT(article_id,user_id) DO UPDATE SET read_at=now()',article_id,actor.user_id)
                await conn.execute('INSERT INTO shoe_workbench.help_onboarding(user_id) VALUES($1) ON CONFLICT DO NOTHING',actor.user_id)
                await conn.execute('''INSERT INTO shoe_workbench.announcement_reads(announcement_id,user_id,read_method,read_at)
                    SELECT id,$2,'opened',now() FROM shoe_workbench.announcements WHERE help_article_id=$1 AND status='published' AND deleted_at IS NULL
                    ON CONFLICT(announcement_id,user_id) DO UPDATE SET read_method='opened',read_at=now()''',article_id,actor.user_id)
        return {'read':True}

    @app.put('/admin/help/articles/{article_id}')
    async def save(article_id:str,payload:ArticleInput,user=Depends(current_user)):
        require_admin(user)
        if len(article_id)>100 or payload.status not in ('draft','published','withdrawn') or not payload.title.strip():raise HTTPException(422,'请检查标题和发布状态')
        if not payload.content.strip() and not payload.images:raise HTTPException(422,'请填写正文或添加教学图片')
        async with app.state.db.acquire() as conn:
            async with conn.transaction():
                count=await conn.fetchval('SELECT count(*) FROM shoe_workbench.attachments WHERE id=ANY($1::text[]) AND feedback_message_id IS NULL AND content_type IN (\'image/png\',\'image/jpeg\')',payload.images)
                if count!=len(set(payload.images)):raise HTTPException(422,'教学图片不存在或类型不正确')
                await conn.execute('''INSERT INTO shoe_workbench.help_articles(id,title,content,images,status,created_by)
                    VALUES($1,$2,$3,$4::jsonb,$5,$6) ON CONFLICT(id) DO UPDATE SET title=$2,content=$3,images=$4::jsonb,status=$5,updated_at=now()''',article_id,payload.title.strip(),payload.content,json.dumps(payload.images),payload.status,user[0].user_id)
                notice_id='help-article-'+article_id
                if payload.status=='published':
                    await conn.execute('''INSERT INTO shoe_workbench.announcements(id,title,content,status,created_by,help_article_id)
                        VALUES($1,$2,$3,'published',$4,$5) ON CONFLICT(id) DO UPDATE SET title=$2,content=$3,status='published',deleted_at=NULL,updated_at=now()''',notice_id,'使用说明：'+payload.title.strip(),(payload.content[:240] or '新增图文操作说明，点击查看教学图片。')+'\n点击「查看使用说明」阅读完整内容。',user[0].user_id,article_id)
                else:
                    await conn.execute("UPDATE shoe_workbench.announcements SET status='withdrawn',updated_at=now() WHERE id=$1",notice_id)
        return {'id':article_id,'saved':True}
