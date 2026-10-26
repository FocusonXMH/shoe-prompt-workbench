"""Common media validation and ownership-checked inline attachment placement."""
import re
import uuid
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

TYPES={'image/png':b'\x89PNG\r\n\x1a\n','image/jpeg':b'\xff\xd8\xff','video/mp4':None,'video/webm':b'\x1aE\xdf\xa3'}

async def read_upload(request):
    kind=request.headers.get('content-type','').split(';',1)[0].lower()
    if kind not in TYPES:raise HTTPException(415,'支持 PNG、JPEG 图片及 MP4、WebM 视频')
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>10*1024*1024:raise HTTPException(413,'每个附件不得超过 10 MB')
    valid=len(body)>=12 and body[4:8]==b'ftyp' if kind=='video/mp4' else body.startswith(TYPES[kind])
    if not valid:raise HTTPException(415,'附件格式与类型不匹配')
    return kind,body

class InlineLinks(BaseModel):
    message_id: str | None = None
    links: dict[str,str] = Field(max_length=50)

def register(app,current_user):
    @app.post('/feedback/{feedback_id}/inline-media')
    async def resolve(feedback_id:str,payload:InlineLinks,user=Depends(current_user)):
        actor=user[0]
        try:links={str(uuid.UUID(k)):str(uuid.UUID(v)) for k,v in payload.links.items()}
        except ValueError:raise HTTPException(422,'附件编号无效')
        async with app.state.db.acquire() as conn:
            async with conn.transaction():
                root=await conn.fetchrow('SELECT author_id,content FROM shoe_workbench.feedback WHERE id=$1 AND deleted_at IS NULL FOR UPDATE',feedback_id)
                if not root:raise HTTPException(404,'反馈不存在')
                row=await conn.fetchrow("SELECT author_id,content FROM shoe_workbench.feedback_messages WHERE id=$1 AND feedback_id=$2 AND status='active' FOR UPDATE",payload.message_id,feedback_id) if payload.message_id else root
                if not row or row['author_id']!=actor.user_id:raise HTTPException(403,'只能调整自己提交的附件位置')
                valid=await conn.fetch('''SELECT a.id FROM shoe_workbench.attachments a JOIN shoe_workbench.feedback_messages m ON m.id=a.feedback_message_id
                    WHERE a.id=ANY($1::text[]) AND a.owner_id=$2 AND m.feedback_id=$3 AND m.status='active'
                    AND ($4::text IS NULL OR m.id=$4)''',list(links.values()),actor.user_id,feedback_id,payload.message_id)
                if {r['id'] for r in valid}!=set(links.values()):raise HTTPException(422,'引用附件不属于本条内容')
                text=row['content']
                for source,target in links.items():text=text.replace('[[media:'+source+']]','[[media:'+target+']]')
                if payload.message_id:await conn.execute('UPDATE shoe_workbench.feedback_messages SET content=$2 WHERE id=$1',payload.message_id,text)
                else:await conn.execute('UPDATE shoe_workbench.feedback SET content=$2 WHERE id=$1',feedback_id,text)
        return {'saved':True}

def preview(value):
    return re.sub(r'\[\[media:[0-9a-f-]{36}\]\]','[附件]',value or '')
