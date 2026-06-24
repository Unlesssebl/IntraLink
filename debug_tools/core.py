import os
import aiohttp
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Пытаемся загрузить переменные из .env
load_dotenv(os.path.join(PROJECT_ROOT, "printer-worker", ".env"))
load_dotenv(os.path.join(PROJECT_ROOT, "core-api", ".env")) # Fallback if variables are there

CORE_API_URL = os.getenv("CORE_API_URL", "http://127.0.0.1:8000/api/v1")
BOT_API_KEY = os.getenv("BOT_API_KEY", "test_api_key_12345")

async def fetch_task(task_id: int) -> dict:
    url = f"{CORE_API_URL.rstrip('/')}/service/tasks/{task_id}"
    headers = {
        "Content-Type": "application/json",
        "X-Bot-Api-Key": BOT_API_KEY
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status in (200, 201):
                data = await response.json()
                # Core API может возвращать {Task: {...}, Statuses: [...]} или саму задачу
                if isinstance(data, dict) and "Task" in data:
                    return data["Task"]
                return data
            else:
                text = await response.text()
                raise RuntimeError(f"Ошибка получения заявки #{task_id}: [{response.status}] {text}")
