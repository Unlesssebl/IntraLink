import asyncio
import redis.asyncio as aioredis
import json

async def main():
    r = aioredis.from_url('redis://127.0.0.1:6379/0', decode_responses=True)
    
    # 1. AI Worker Stats
    print('--- AI Worker Stats ---')
    try:
        ai_stats = await r.hgetall('ai:stats')
        print(ai_stats)
    except Exception as e:
        print('No AI stats:', e)
        
    # 2. Printer Worker Jobs
    print('\n--- Printer Worker FAILED Jobs ---')
    try:
        keys = await r.keys('printer_job:*')
        failed = []
        for k in keys:
            data = await r.get(k)
            if data:
                job = json.loads(data)
                # print(job.get('state'))
                if job.get('state') == 'failed': # JobState.FAILED
                    failed.append(job)
        print(f'Total printer jobs in Redis: {len(keys)}')
        print(f'Failed printer jobs: {len(failed)}')
        for f in failed[:15]:
            print(f"Task {f.get('task_id')}: {f.get('error_message')} (Target: {f.get('target_pc')}, Model: {f.get('model_key')})")
    except Exception as e:
        print('Error reading printer jobs:', e)

if __name__ == '__main__':
    asyncio.run(main())
