import os
import json
import random
import asyncio
import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from app.routers.deps import verify_api_key
from app.services.worker import get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin UI"])

# Вспомогательный класс для ручного запуска задачи
class ManualJobRequest(BaseModel):
    target_pc: str
    model_key: str
    connection_type: str
    printer_address: Optional[str] = None

# Путь к файлу базы знаний
KB_PATH = os.environ.get(
    "PRINTERS_KB_PATH",
    os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "printer-worker", "knowledge_base", "printers_knowledge_base.json"
    ))
)

@router.get("/admin", response_class=HTMLResponse)
async def get_admin_ui():
    """
    Отдает HTML-страницу админ-панели.
    """
    html_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "admin", "index.html"))
    if not os.path.exists(html_path):
        # Если папки static/admin не существует, создадим её и отдадим дефолтную страницу заглушки
        return HTMLResponse(
            content="<h1>Панель администратора не найдена на сервере.</h1><p>Пожалуйста, убедитесь, что static/admin/index.html существует.</p>",
            status_code=status.HTTP_404_NOT_FOUND
        )
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except Exception as e:
        logger.exception("Ошибка при чтении файла шаблона админ-панели: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сервера при загрузке UI: {e}"
        )

@router.get("/admin/api/worker-status", dependencies=[Depends(verify_api_key)])
async def get_worker_status():
    """
    Возвращает текущий статус printer-worker (online/offline).
    """
    try:
        r = get_redis_client()
        status_val = await r.get("printer_worker:status")
        return {"status": "online" if status_val == "online" else "offline"}
    except Exception as e:
        logger.exception("Ошибка проверки статуса воркера: %s", e)
        return {"status": "offline", "error": str(e)}

@router.get("/admin/api/knowledge-base", dependencies=[Depends(verify_api_key)])
async def get_knowledge_base():
    """
    Считывает и отдает базу знаний принтеров.
    """
    if not os.path.exists(KB_PATH):
        logger.error("Файл базы знаний не найден по пути: %s", KB_PATH)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл базы знаний не найден на сервере."
        )
    try:
        with open(KB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        logger.exception("Ошибка парсинга базы знаний: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка парсинга JSON базы знаний."
        )

@router.get("/admin/api/print-jobs", dependencies=[Depends(verify_api_key)])
async def get_print_jobs():
    """
    Возвращает список недавних задач из Redis.
    """
    try:
        r = get_redis_client()
        # Получаем последние 50 ID задач из sorted set
        task_ids = await r.zrevrange("printer_jobs_list", 0, 49)
        
        jobs = []
        if task_ids:
            # Получаем все данные задач в один pipeline
            pipe = r.pipeline()
            for tid in task_ids:
                pipe.get(f"printer_job:{tid}")
            results = await pipe.execute()
            
            for data in results:
                if data:
                    try:
                        jobs.append(json.loads(data))
                    except Exception:
                        pass
        return jobs
    except Exception as e:
        logger.exception("Ошибка получения списка задач из Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка Redis: {e}"
        )

@router.post("/admin/api/print-jobs", dependencies=[Depends(verify_api_key)])
async def trigger_manual_job(payload: ManualJobRequest):
    """
    Вручную инициирует установку принтера, отправляя событие в Redis.
    """
    try:
        r = get_redis_client()
        # Генерируем случайный ID для ручного задания (8 знаков)
        task_id = random.randint(10000000, 99999999)
        
        event = {
            "event_type": "manual_trigger",
            "task_id": task_id,
            "tg_user_id": 0,
            "target_pc": payload.target_pc.strip(),
            "model_key": payload.model_key,
            "connection_type": payload.connection_type,
            "printer_address": payload.printer_address.strip() if payload.printer_address else None
        }
        
        # Публикуем событие для printer-worker
        await r.publish("printer_actions", json.dumps(event))
        logger.info("Вручную запущена задача #%d для ПК %s", task_id, payload.target_pc)
        return {"status": "success", "task_id": task_id}
    except Exception as e:
        logger.exception("Ошибка публикации ручной задачи в Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось запустить задачу: {e}"
        )

async def log_stream_generator(job_id: int):
    """
    Генератор для SSE-стрима логов.
    """
    r = get_redis_client()
    pubsub = r.pubsub()
    await pubsub.subscribe(f"printer_job_logs:{job_id}")
    
    yield "event: message\ndata: [SYSTEM] Подключение к потоку логов... ожидание вывода воркера.\n\n"
    
    # Отправим также текущий стейт задачи, если он есть
    job_data_raw = await r.get(f"printer_job:{job_id}")
    if job_data_raw:
        try:
            job_data = json.loads(job_data_raw)
            state = job_data.get("state", "unknown")
            error_message = job_data.get("error_message")
            msg = f"[SYSTEM] Текущее состояние задачи: {state}"
            if error_message:
                msg += f" (Ошибка: {error_message})"
            yield f"event: message\ndata: {msg}\n\n"
        except Exception:
            pass

    try:
        while True:
            # Считываем из Pub/Sub с таймаутом
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message:
                log_line = message.get("data")
                if log_line:
                    yield f"event: message\ndata: {log_line}\n\n"
            else:
                # Отправляем ping каждые 15 секунд для удержания SSE соединения открытым
                await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        logger.info("SSE клиент отключился от логов задачи #%d", job_id)
    finally:
        await pubsub.unsubscribe(f"printer_job_logs:{job_id}")
        await pubsub.close()

@router.get("/admin/api/print-jobs/{job_id}/logs", dependencies=[Depends(verify_api_key)])
async def stream_job_logs(job_id: int):
    """
    SSE-эндпоинт для прослушивания логов конкретного задания в реальном времени.
    """
    return StreamingResponse(
        log_stream_generator(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
