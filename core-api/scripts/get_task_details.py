import asyncio
import os
import sys
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.services import intraservice
from app.services.worker import get_redis_client

async def get_details(task_id: int):
    auth_b64 = None
    
    # 1. Попробуем из конфига
    service_login = settings.INTRASERVICE_SERVICE_LOGIN
    service_password = settings.INTRASERVICE_SERVICE_PASSWORD
    if service_login and service_password:
        auth_b64, _ = await intraservice.verify_credentials(service_login, service_password)
        
    # 2. Попробуем из Redis
    if not auth_b64:
        try:
            redis = get_redis_client()
            encrypted_auth = await redis.get("worker:service_auth_b64")
            if encrypted_auth:
                if isinstance(encrypted_auth, bytes):
                    encrypted_auth = encrypted_auth.decode()
                auth_b64 = encrypted_auth
                print("Using service credentials from Redis")
        except Exception as e:
            print(f"Error reading credentials from Redis: {e}")

    if not auth_b64:
        print("Error: auth_b64 credentials not found anywhere")
        return

    await intraservice.init_session()
    try:
        task = await intraservice.get_single_task(auth_b64, task_id)
        comments = await intraservice.get_task_comments(auth_b64, task_id)
        
        result = {
            "task": task,
            "comments": comments
        }
        
        output_file = f"/app/scripts/task_{task_id}_data.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"Successfully saved details to {output_file}")
    finally:
        await intraservice.close_session()

if __name__ == "__main__":
    task_id = 132437
    if len(sys.argv) > 1:
        task_id = int(sys.argv[1])
    asyncio.run(get_details(task_id))
