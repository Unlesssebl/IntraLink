import asyncio
import json
import logging
import time
from typing import List
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.responses import StreamingResponse

from app.routers.deps import verify_admin_jwt
from app.services.worker import get_redis_client
from app.config import settings
from app.services.ai_responder import AIResponder
from app.services.rag_builder import build_rag_dataset
from app.services.intraservice import get_single_task, verify_credentials

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Control"])

PING_INTERVAL = 15.0


class AIConfigUpdate(BaseModel):
    auto_reply_service_ids: List[int] = Field(..., description="Список ID разделов для автоответов")
    auto_reply_mode: str = Field(..., description="Режим автоответа: comment_only | comment_and_wait | comment_and_resolve")
    printer_service_ids: List[int] = Field(..., description="Список ID разделов для printer-worker")


@router.get("/admin/api/ai-worker/status", dependencies=[Depends(verify_admin_jwt)])
async def get_ai_status():
    """
    Возвращает текущие метрики работы AI и конфигурацию из Redis (с fallback на settings).
    """
    try:
        r = get_redis_client()
        
        # Чтение метрик
        stats = await r.hgetall("ai:stats")
        
        # Чтение конфигурации из Redis с fallback на settings
        auto_reply_services_str = await r.get("config:auto_reply_service_ids")
        if auto_reply_services_str:
            auto_reply_service_ids = json.loads(auto_reply_services_str)
        else:
            auto_reply_service_ids = settings.AUTO_REPLY_SERVICE_IDS

        auto_reply_mode = await r.get("config:auto_reply_mode") or settings.AUTO_REPLY_MODE
        
        printer_services_str = await r.get("config:printer_service_ids")
        if printer_services_str:
            printer_service_ids = json.loads(printer_services_str)
        else:
            printer_service_ids = settings.PRINTER_SERVICE_IDS

        # Проверка статуса запуска RAG
        rag_running = await r.get("rag_build:running") == "true"

        return {
            "metrics": {
                "classifications": int(stats.get("classifications", 0)),
                "redirected": int(stats.get("redirected", 0)),
                "replied": int(stats.get("replied", 0)),
                "total": int(stats.get("total", 0)),
                "last_reply_task_id": stats.get("last_reply_time", "N/A")
            },
            "config": {
                "auto_reply_service_ids": auto_reply_service_ids,
                "auto_reply_mode": auto_reply_mode,
                "printer_service_ids": printer_service_ids
            },
            "rag_running": rag_running
        }
    except Exception as e:
        logger.exception("Ошибка при получении статуса AI-воркера: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сервера: {e}",
        )


@router.post("/admin/api/ai-worker/config", dependencies=[Depends(verify_admin_jwt)])
async def update_ai_config(payload: AIConfigUpdate):
    """
    Обновляет настройки AI-воркера на лету в Redis.
    """
    if payload.auto_reply_mode not in ("comment_only", "comment_and_wait", "comment_and_resolve"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный режим автоответа (auto_reply_mode)",
        )
    try:
        r = get_redis_client()
        await r.set("config:auto_reply_service_ids", json.dumps(payload.auto_reply_service_ids))
        await r.set("config:auto_reply_mode", payload.auto_reply_mode)
        await r.set("config:printer_service_ids", json.dumps(payload.printer_service_ids))
        
        logger.info(
            "Конфигурация AI-воркера обновлена администратором: auto_reply_services=%s, mode=%s, printer_services=%s",
            payload.auto_reply_service_ids, payload.auto_reply_mode, payload.printer_service_ids
        )
        return {"status": "success"}
    except Exception as e:
        logger.exception("Ошибка при сохранении конфигурации AI в Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось обновить конфигурацию: {e}",
        )


@router.post("/admin/api/ai-worker/test-reply/{task_id}", dependencies=[Depends(verify_admin_jwt)])
async def generate_test_reply(task_id: int):
    """
    Генерирует тестовый автоответ для задачи без реальной отправки в IntraService.
    """
    try:
        r = get_redis_client()
        
        # Получаем зашифрованные учетные данные из Redis
        encrypted_auth = await r.get("worker:service_auth_b64")
        if not encrypted_auth:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Учетные данные сервисного аккаунта отсутствуют. Пожалуйста, выполните повторный вход.",
            )
            
        from app.services.crypto import decrypt_token
        auth_b64 = decrypt_token(encrypted_auth)
        
        # Загружаем подробности задачи
        raw_response = await get_single_task(auth_b64, task_id)
            
        if not raw_response:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Задача #{task_id} не найдена в IntraService",
            )
            
        task_data = raw_response
        if isinstance(raw_response, dict) and "Task" in raw_response:
            task_data = raw_response["Task"]
            
        # Запускаем генерацию
        responder = AIResponder()
        result = await responder.generate_reply(task_data)
        
        return {
            "task_id": task_id,
            "task_name": task_data.get("Name"),
            "task_description": task_data.get("Description"),
            "service_name": task_data.get("ServiceName"),
            "service_id": task_data.get("ServiceId"),
            "generated_reply": result.reply_text,
            "confidence": result.confidence,
            "can_resolve": result.can_resolve,
            "needs_clarification": result.needs_clarification,
            "reason": result.reason
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка при генерации тестового автоответа для задачи #%d: %s", task_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка генерации: {e}",
        )


@router.post("/admin/api/ai-worker/rag/build", dependencies=[Depends(verify_admin_jwt)])
async def trigger_rag_build(limit: int = 50):
    """
    Запускает фоновый процесс перестроения базы знаний RAG.
    """
    r = get_redis_client()
    
    # Проверяем, не запущен ли уже процесс
    if await r.get("rag_build:running") == "true":
        return {"status": "already_running", "message": "Процесс перестроения базы RAG уже запущен."}
        
    encrypted_auth = await r.get("worker:service_auth_b64")
    if not encrypted_auth:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Сервисный аккаунт не настроен. Пожалуйста, переавторизуйтесь в панели.",
        )
        
    from app.services.crypto import decrypt_token
    auth_b64 = decrypt_token(encrypted_auth)

    # Функция обратного вызова для логирования прогресса в Redis
    async def progress_cb(msg: str):
        try:
            # Публикуем в канал и пишем в историю
            await r.publish("rag_build_logs", msg)
            await r.rpush("rag_build_logs_history", msg)
            await r.expire("rag_build_logs_history", 86400)
        except Exception:
            pass

    # Фоновое выполнение задачи
    async def background_task():
        try:
            await r.set("rag_build:running", "true")
            await r.delete("rag_build_logs_history")
            await build_rag_dataset(limit_tasks=limit, auth_b64=auth_b64, progress_callback=progress_cb)
        except Exception as e:
            logger.exception("Сбой фонового перестроения базы знаний RAG: %s", e)
            try:
                await progress_cb(f"[ERROR] Критическая ошибка: {e}")
            except Exception:
                pass
        finally:
            await r.set("rag_build:running", "false")
            try:
                await progress_cb("[SYSTEM] Процесс перестроения базы RAG завершен.")
            except Exception:
                pass

    asyncio.create_task(background_task())
    return {"status": "success", "message": "Процесс перестроения базы RAG запущен в фоновом режиме."}


async def rag_log_generator():
    """
    Генератор SSE для вывода логов перестроения RAG.
    """
    r = get_redis_client()
    pubsub = r.pubsub()
    await pubsub.subscribe("rag_build_logs")

    yield "event: message\ndata: [SYSTEM] Подключение к потоку логов RAG...\n\n"

    # Отдаем накопившиеся строки логов
    try:
        history = await r.lrange("rag_build_logs_history", 0, -1)
        if history:
            for line in history:
                yield f"event: message\ndata: {line}\n\n"
    except Exception as e:
        logger.error("Ошибка при получении истории логов RAG: %s", e)

    last_msg_time = time.monotonic()
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message:
                log_line = message.get("data")
                if log_line:
                    yield f"event: message\ndata: {log_line}\n\n"
                    last_msg_time = time.monotonic()
            else:
                if time.monotonic() - last_msg_time > PING_INTERVAL:
                    yield ": ping\n\n"
                    last_msg_time = time.monotonic()
                await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe("rag_build_logs")
        await pubsub.close()


@router.get("/admin/api/ai-worker/rag/logs", dependencies=[Depends(verify_admin_jwt)])
async def stream_rag_logs():
    """
    SSE эндпоинт для логов сборщика RAG в реальном времени.
    """
    return StreamingResponse(
        rag_log_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
