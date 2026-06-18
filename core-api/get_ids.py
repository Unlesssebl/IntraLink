import asyncio
import redis.asyncio as redis
import json

async def get_ids():
    r = redis.from_url('redis://localhost:6379', decode_responses=True)
    cat = await r.get('worker:service_catalog')
    if cat:
        data = json.loads(cat)
        for item in data:
            name = item.get('name', '')
            if 'Технотрон' in name or '16' in name or 'IPS' in name:
                print(f"{name}: {item['id']}")
    await r.aclose()

asyncio.run(get_ids())
