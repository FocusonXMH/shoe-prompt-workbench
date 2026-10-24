"""Import bundled prototype templates into an empty isolated workbench only."""
import asyncio
import json
import os
from pathlib import Path

import asyncpg


async def main():
    root = Path(__file__).resolve().parents[1]
    dsn = os.environ['SHOE_DATABASE_URL']
    seed = json.loads((root / 'seed.json').read_text(encoding='utf-8'))
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            # Advisory lock prevents a second seed from racing this one.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('shoe_workbench_initial_seed'))")
            if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM shoe_workbench.templates) OR EXISTS(SELECT 1 FROM shoe_workbench.selling_points)'):
                print('workbench not empty; seed skipped without changes')
                return
            for point in seed['points']:
                await conn.execute('INSERT INTO shoe_workbench.selling_points(id,title,description,position) VALUES($1,$2,$3,$4)',
                                   point['id'],point['name'],point.get('description',''),point.get('order',0))
            for template in seed['templates']:
                published = template.get('status') == 'published'
                content = dict(template)
                # The body itself is persisted with the snapshot, not a reference to a JS file.
                content['sourceKey'] = None
                content['version'] = 1 if published else 0
                selling_point = template['pointId'] if template['pointId'] != 'general' else None
                draft = {'title':template['title'], 'media_type':template['type'],
                         'selling_point_id':selling_point, 'content':content}
                await conn.execute('''INSERT INTO shoe_workbench.templates(id,title,media_type,selling_point_id,position,draft,published_version)
                    VALUES($1,$2,$3,$4,$5,$6::jsonb,$7)''', template['id'],template['title'],template['type'],selling_point,
                    template.get('order',0),json.dumps(draft,ensure_ascii=False),1 if published else None)
                if published:
                    await conn.execute('''INSERT INTO shoe_workbench.template_versions(template_id,version,content,published_by)
                        VALUES($1,1,$2::jsonb,'initial-seed')''', template['id'],json.dumps(content,ensure_ascii=False))
        print(f"seeded {len(seed['points'])} points and {len(seed['templates'])} templates")
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())
