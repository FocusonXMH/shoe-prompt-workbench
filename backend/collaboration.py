"""Thread-local anonymity and administrator inbox delivery."""
import uuid
import re
import json
from zoneinfo import ZoneInfo

async def anonymous_map(conn,feedback_id):
    rows=await conn.fetch('''SELECT author_id,created_at,id FROM shoe_workbench.feedback WHERE id=$1 AND anonymous_to_users
      UNION ALL SELECT author_id,created_at,id FROM shoe_workbench.feedback_messages WHERE feedback_id=$1 AND anonymous_to_users
      ORDER BY created_at,id''',feedback_id)
    result={}
    for row in rows:
        if row['author_id'] not in result: result[row['author_id']]=len(result)+1
    return result

async def decorate(conn,item,actor,feedback_id,anonymous,*,aliases=None,profiles=None):
    if isinstance(item.get('template_reference'),str):item['template_reference']=json.loads(item['template_reference'])
    author=item['author_id'];item['is_owner']=author==actor.user_id
    # Cache only within one response, never across users or requests.
    if profiles is None: profiles={}
    if author not in profiles:
        profiles[author]=await conn.fetchrow('SELECT display_name,category,username,role_code FROM shoe_workbench.user_profiles WHERE user_id=$1',author)
    profile=profiles[author]
    name=(profile['display_name'] if profile else None) or '用户'
    if anonymous:
        if aliases is None: aliases=await anonymous_map(conn,feedback_id)
        number=aliases.get(author,1)
        item['author_name']='匿名'+str(number)
        if actor.is_admin: item['author_name']+='（'+name+'）'
        item['author_id']=author if actor.user_id==author else None
    else:
        category=(profile['category'] if profile else None) or ({'operator':'运营','ai':'AI','management':'管理','super_admin':'管理'}.get(profile['role_code'],'') if profile else '')
        item['author_name']=((category+' · ') if category else '')+name
    return item

async def notification_context(conn,feedback_id,title,message_id=None):
    root=await conn.fetchrow('SELECT category,content,status,author_id,created_at FROM shoe_workbench.feedback WHERE id=$1 AND deleted_at IS NULL',feedback_id)
    if not root:return None
    latest=await conn.fetchrow("SELECT id,content,author_id FROM shoe_workbench.feedback_messages WHERE feedback_id=$1 AND status='active' AND ($2::text IS NULL OR id=$2) ORDER BY created_at DESC,id DESC LIMIT 1",feedback_id,message_id)
    snippet=lambda value,limit:' '.join((value or '').split())[:limit]
    status={'pending':'待解决','processing':'处理中','resolved':'已解决','awaiting_confirmation':'待确认','withdrawn':'已撤回'}.get(root['status'],root['status'])
    context=re.match(r'^【(?:位置|卖点|模板)：([^】]+)】\s*',root['content'])
    question=root['content'][context.end():] if context else root['content']
    aliases=await anonymous_map(conn,feedback_id)
    async def person(ident):
        if ident in aliases:return '匿名'+str(aliases[ident])
        row=await conn.fetchrow('SELECT display_name,category,role_code FROM shoe_workbench.user_profiles WHERE user_id=$1',ident)
        if not row:return '用户'
        category=row['category'] or {'operator':'运营','ai':'AI','management':'管理','super_admin':'管理'}.get(row['role_code'],'')
        return (category+' · ' if category else '')+(row['display_name'] or '用户')
    if '恢复' in title:heading='回复已恢复'
    elif '状态' in title:heading='反馈状态已更新'
    elif '确认' in title:heading='反馈待确认'
    elif '回复' in title:heading='反馈有新回复'
    elif '进展' in title or '管理员' in title:heading='管理员更新了反馈'
    elif '待处理' in title:heading='待处理反馈'
    else:heading='收到新反馈'
    body='问题：'+snippet(question or root['content'],180)+'\n'
    if latest:body+='回复：'+snippet(latest['content'],200)+'\n回复人：'+await person(latest['author_id'])+'\n'
    body+='发起人：'+await person(root['author_id'])+'\n'
    if context:body+='位置：'+snippet(context[1],100)+'\n'
    body+='发起时间：'+root['created_at'].astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')+'\n当前状态：'+status
    return (heading,body,
        latest['id'] if latest else None)

async def notify_admins(conn,shared_pool,feedback_id,actor,title,message_id=None):
    context=await notification_context(conn,feedback_id,title,message_id)
    if not context:return
    ids=set()
    if shared_pool:
        async with shared_pool.acquire() as shared:
            ids.update(str(r['user_id']) for r in await shared.fetch("SELECT user_id FROM iam.users WHERE role_code IN ('management','super_admin') AND status='active'"))
    ids.update(r['id'] for r in await conn.fetch("SELECT id FROM shoe_workbench.local_accounts WHERE role_code IN ('management','super_admin') AND active"))
    for user_id in ids:
        if user_id==actor.user_id: continue
        await conn.execute('''INSERT INTO shoe_workbench.announcements(id,title,content,audience_user_id,status,created_by,feedback_id,feedback_message_id)
          VALUES($1,$2,$3,$4,'published',$5,$6,$7)''','feedback-notice-'+str(uuid.uuid4()),context[0],
          context[1],user_id,actor.user_id,feedback_id,context[2])
