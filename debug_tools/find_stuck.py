import asyncio
import json
import redis.asyncio as aioredis

async def main():
    r = aioredis.from_url('redis://127.0.0.1:6379/0', decode_responses=True)
    keys = await r.keys('printer_job:*')
    stuck = []
    for k in keys:
        data = await r.get(k)
        if data:
            job = json.loads(data)
            if job.get('state') not in ('done', 'failed', 'waiting_approval'):
                stuck.append(job)
    
    print(f"Found {len(stuck)} stuck jobs.")
    for j in stuck:
        print(f"Task {j['task_id']}: state={j['state']}, error={j.get('error_message')}")
    await r.close()

if __name__ == "__main__":
    asyncio.run(main())
