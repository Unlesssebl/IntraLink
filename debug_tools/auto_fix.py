import asyncio
import redis.asyncio as aioredis
import json
import logging

logging.basicConfig(level=logging.INFO)

async def main():
    r = aioredis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)
    
    tasks_to_restart = []
    
    async for key in r.scan_iter("printer_job:*"):
        data = await r.get(key)
        if data:
            try:
                job = json.loads(data)
                if job.get("state") in ("failed", "waiting_approval"):
                    tasks_to_restart.append(job.get("task_id"))
            except Exception as e:
                pass
                
    print(f"🚀 Found {len(tasks_to_restart)} failed or stuck tasks. Restarting them with the new AI parsing logic...")
    for tid in sorted(tasks_to_restart):
        payload = {
            "event_type": "status_change",
            "task_id": tid,
            "status_id": 31,
            "tg_user_id": 0
        }
        # Delete the old state first so it starts fresh!
        await r.delete(f"printer_job:{tid}")
        
        await r.publish("intraservice_events", json.dumps(payload))
        print(f"✅ Restarted task #{tid}")
        await asyncio.sleep(0.5)
        
    await r.close()
    print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
