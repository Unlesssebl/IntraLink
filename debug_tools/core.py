import os
import json
import aiohttp
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, "printer-worker", ".env"))
load_dotenv(os.path.join(PROJECT_ROOT, "core-api", ".env"))

CORE_API_URL = os.getenv("CORE_API_URL", "http://127.0.0.1:8000/api/v1")
BOT_API_KEY = os.getenv("BOT_API_KEY", "test_api_key_12345")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")


async def fetch_task(task_id: int) -> dict:
    """Загружает заявку через Core API."""
    url = f"{CORE_API_URL.rstrip('/')}/service/tasks/{task_id}"
    headers = {"Content-Type": "application/json", "X-Bot-Api-Key": BOT_API_KEY}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status in (200, 201):
                data = await response.json()
                if isinstance(data, dict) and "Task" in data:
                    return data["Task"]
                return data
            else:
                text = await response.text()
                raise RuntimeError(f"Ошибка получения заявки #{task_id}: [{response.status}] {text}")


async def fetch_task_from_redis(task_id: int) -> dict | None:
    """
    Fallback: читает данные задачи из Redis-кэша (ключ printer_job:{task_id}).
    Возвращает None если задача не найдена в кэше.
    """
    import redis.asyncio as aioredis
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        raw = await r.get(f"printer_job:{task_id}")
        if raw:
            return json.loads(raw)
        return None
    finally:
        await r.close()


async def fetch_task_auto(task_id: int, from_redis: bool = False) -> dict:
    """
    Умная загрузка данных заявки:
    - Если from_redis=True — читает из Redis-кэша (без Core API)
    - Иначе — пробует Core API, при ошибке пробует Redis-кэш как fallback
    """
    if from_redis:
        data = await fetch_task_from_redis(task_id)
        if not data:
            raise RuntimeError(f"Задача #{task_id} не найдена в Redis-кэше (printer_job:{task_id})")
        return data

    try:
        return await fetch_task(task_id)
    except Exception as api_err:
        print(f"⚠️  Core API недоступен ({api_err}), пробую Redis-кэш...")
        data = await fetch_task_from_redis(task_id)
        if data:
            print(f"   ✅ Данные загружены из Redis-кэша.")
            return data
        raise RuntimeError(f"Задача #{task_id} недоступна: Core API ({api_err}), Redis-кэш — пуст.")
