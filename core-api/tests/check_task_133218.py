import asyncio
import sys
import os
import json

# Добавляем путь к core-api в sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.intraservice import get_single_task, get_task_lifetime, init_session, close_session
from app.config import settings

async def main():
    auth_b64 = "SW50cmFUZXN0Ojg1X3dXOEV1T3lZYXcreHY2"
    task_id = 133218
    
    await init_session()
    try:
        print("Fetching task details...")
        raw_task = await get_single_task(auth_b64, task_id)
        if not raw_task:
            print("Failed to fetch task details")
            return
            
        print("Raw Task JSON:")
        print(json.dumps(raw_task, indent=2, ensure_ascii=False))
        
        print("\nFetching task lifetime...")
        lifetime = await get_task_lifetime(auth_b64, task_id)
        print("Raw Lifetime JSON:")
        print(json.dumps(lifetime, indent=2, ensure_ascii=False))
    finally:
        await close_session()

if __name__ == "__main__":
    asyncio.run(main())
