import asyncio
import json
import logging
import time
from typing import List, Dict
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, delete, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers.deps import verify_admin_jwt
from app.services.worker import get_redis_client
from app.config import settings
from app.database.db import get_db, TaskKnowledgeBase

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Control"])

PING_INTERVAL = 15.0


def build_tree(flat_services: list[dict]) -> list[dict]:
    # Строим карту узлов
    nodes = {s["id"]: {**s, "children": []} for s in flat_services}
    tree = []
    for s_id, node in nodes.items():
        parent_id = node.get("parent_id")
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            tree.append(node)
    return tree


class AIConfigUpdate(BaseModel):
    auto_reply_service_ids: List[int] = Field(..., description="Список ID разделов для автоответов")
    auto_reply_mode: str = Field(..., description="Режим автоответа: comment_only | comment_and_wait | comment_and_resolve")

@router.get("/admin/api/services-tree", dependencies=[Depends(verify_admin_jwt)])
async def get_services_tree():
    """
    Возвращает каталог услуг в виде иерархического дерева с чекбоксами.
    """
    try:
        r = get_redis_client()
        catalog_str = await r.get("worker:service_catalog")
        
        # Если в Redis пусто, пробуем синхронизировать
        if not catalog_str:
            from app.services.worker import sync_service_catalog
            await sync_service_catalog()
            catalog_str = await r.get("worker:service_catalog")
            
        if not catalog_str:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Каталог услуг еще не синхронизирован. Пожалуйста, подождите."
            )
            
        flat_catalog = json.loads(catalog_str)
        return build_tree(flat_catalog)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка при построении дерева услуг: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сервера: {e}"
        )


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
                "auto_reply_mode": auto_reply_mode
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
        
        logger.info(
            "Конфигурация AI-воркера обновлена администратором: auto_reply_services=%s, mode=%s",
            payload.auto_reply_service_ids, payload.auto_reply_mode
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
    Генерирует тестовый автоответ для задачи, пересылая команду в ai-worker по Redis.
    """
    import secrets
    r = get_redis_client()
    req_id = secrets.token_hex(8)
    
    # Отправляем команду в ai-worker
    payload = {
        "event_type": "test_reply",
        "task_id": task_id,
        "req_id": req_id
    }
    
    try:
        await r.publish("ai_actions", json.dumps(payload))
        
        # Ждем ответ в Redis в течение 7 секунд
        for _ in range(70):
            await asyncio.sleep(0.1)
            response_data = await r.get(f"ai:test_reply:{req_id}")
            if response_data:
                result = json.loads(response_data)
                await r.delete(f"ai:test_reply:{req_id}")
                
                if result.get("status") == "error":
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=result.get("message")
                    )
                return result
                
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Превышено время ожидания ответа от ai-worker. Проверьте, запущен ли контейнер ai-worker."
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка при генерации тестового автоответа для задачи #%d: %s", task_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка генерации: {e}",
        )


class RAGQuotasUpdate(BaseModel):
    filter_id: int = Field(..., description="ID фильтра '1 линия' в IntraService")
    global_quotas: Dict[str, int] = Field(..., description="Глобальные квоты по статусам, например {'28': 10, '30': 5}")
    service_quotas: Dict[str, Dict[str, int]] = Field(..., description="Индивидуальные квоты для услуг, например {'123': {'28': 5}}")


@router.post("/admin/api/ai-worker/rag/build", dependencies=[Depends(verify_admin_jwt)])
async def trigger_rag_build(service_ids: list[int] | None = None):
    """
    Запускает фоновый процесс перестроения базы знаний RAG, публикуя команду для ai-worker.
    Передает список услуг, которые нужно перестроить (или None для всех выбранных в AI Config).
    """
    r = get_redis_client()
    
    # Проверяем, не запущен ли уже процесс
    if await r.get("rag_build:running") == "true":
        return {"status": "already_running", "message": "Процесс перестроения базы RAG уже запущен."}
        
    # Читаем квоты из Redis для передачи в Payload
    filter_id_str = await r.get("config:rag_filter_id")
    filter_id = int(filter_id_str) if filter_id_str else 0
    
    global_quotas_str = await r.get("config:rag_global_quotas")
    global_quotas = json.loads(global_quotas_str) if global_quotas_str else {"28": 10, "30": 5}
    
    service_quotas_str = await r.get("config:rag_service_quotas")
    service_quotas = json.loads(service_quotas_str) if service_quotas_str else {}
    
    # Если service_ids не передан, берем те, что отмечены в auto_reply_service_ids
    if service_ids is None:
        auto_reply_services_str = await r.get("config:auto_reply_service_ids")
        service_ids = json.loads(auto_reply_services_str) if auto_reply_services_str else []
        
    # Публикуем команду для ai-worker
    payload = {
        "event_type": "rag_build",
        "filter_id": filter_id,
        "global_quotas": global_quotas,
        "service_quotas": service_quotas,
        "service_ids": service_ids
    }
    
    try:
        await r.publish("ai_actions", json.dumps(payload))
        return {"status": "success", "message": "Команда на перестроение базы RAG успешно отправлена в ai-worker."}
    except Exception as e:
        logger.exception("Ошибка отправки команды RAG в ai-worker: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось запустить процесс: {e}"
        )


@router.get("/admin/api/ai-worker/rag/examples", dependencies=[Depends(verify_admin_jwt)])
async def get_rag_examples(
    page: int = 1,
    limit: int = 20,
    service_id: int | None = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Возвращает примеры из базы знаний, которые не находятся в черном списке.
    Поддерживает пагинацию и фильтрацию по разделу.
    """
    try:
        offset = (page - 1) * limit
        query = select(TaskKnowledgeBase).where(TaskKnowledgeBase.is_blacklisted == False)
        
        if service_id is not None:
            query = query.where(TaskKnowledgeBase.service_id == service_id)
            
        # Считаем общее число записей
        count_query = select(func.count(TaskKnowledgeBase.task_id)).where(TaskKnowledgeBase.is_blacklisted == False)
        if service_id is not None:
            count_query = count_query.where(TaskKnowledgeBase.service_id == service_id)
            
        total = await db.scalar(count_query) or 0
        
        query = query.order_by(TaskKnowledgeBase.task_id.desc()).offset(offset).limit(limit)
        result = await db.execute(query)
        examples = result.scalars().all()
        
        return {
            "total": total,
            "page": page,
            "limit": limit,
            "examples": [
                {
                    "task_id": e.task_id,
                    "original_name": e.original_name,
                    "problem": e.problem,
                    "solution": e.solution,
                    "service_id": e.service_id,
                    "service_name": e.service_name,
                    "status_name": e.status_name
                } for e in examples
            ]
        }
    except Exception as e:
        logger.exception("Ошибка при получении примеров RAG: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сервера при чтении примеров: {e}"
        )


@router.delete("/admin/api/ai-worker/rag/examples/{task_id}", dependencies=[Depends(verify_admin_jwt)])
async def delete_rag_example(task_id: int, db: AsyncSession = Depends(get_db)):
    """
    Удаляет пример из базы знаний (помечает как is_blacklisted = True и сбрасывает тексты/эмбеддинг).
    """
    try:
        query = select(TaskKnowledgeBase).where(TaskKnowledgeBase.task_id == task_id)
        result = await db.execute(query)
        example = result.scalar_one_or_none()
        
        if not example:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Пример не найден в базе знаний."
            )
            
        # Помечаем как blacklisted, очищаем тяжелые/семантические поля
        example.is_blacklisted = True
        example.embedding = None
        example.problem = ""
        example.solution = ""
        
        await db.commit()
        logger.info("Задача #%d успешно занесена в черный список RAG", task_id)
        return {"status": "success", "task_id": task_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка при удалении примера RAG: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сервера при удалении: {e}"
        )


@router.get("/admin/api/ai-worker/rag/stats", dependencies=[Depends(verify_admin_jwt)])
async def get_rag_stats(db: AsyncSession = Depends(get_db)):
    """
    Возвращает статистику сбора примеров по услугам и статусам.
    Формат: {service_id: {status_name: count, "total": total}}
    """
    try:
        query = select(
            TaskKnowledgeBase.service_id,
            TaskKnowledgeBase.status_name,
            func.count(TaskKnowledgeBase.task_id)
        ).where(TaskKnowledgeBase.is_blacklisted == False).group_by(
            TaskKnowledgeBase.service_id,
            TaskKnowledgeBase.status_name
        )
        
        result = await db.execute(query)
        rows = result.all()
        
        stats = {}
        for service_id, status_name, cnt in rows:
            if service_id not in stats:
                stats[service_id] = {"total": 0}
            stats[service_id][status_name] = cnt
            stats[service_id]["total"] += cnt
            
        return stats
    except Exception as e:
        logger.exception("Ошибка при подсчете статистики RAG: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сервера: {e}"
        )


@router.get("/admin/api/ai-worker/rag/quotas", dependencies=[Depends(verify_admin_jwt)])
async def get_rag_quotas():
    """
    Возвращает текущие настройки квот RAG из Redis с дефолтными значениями.
    """
    try:
        r = get_redis_client()
        filter_id_str = await r.get("config:rag_filter_id")
        filter_id = int(filter_id_str) if filter_id_str else 0
        
        global_quotas_str = await r.get("config:rag_global_quotas")
        global_quotas = json.loads(global_quotas_str) if global_quotas_str else {"28": 10, "30": 5}
        
        service_quotas_str = await r.get("config:rag_service_quotas")
        service_quotas = json.loads(service_quotas_str) if service_quotas_str else {}
        
        return {
            "filter_id": filter_id,
            "global_quotas": global_quotas,
            "service_quotas": service_quotas
        }
    except Exception as e:
        logger.exception("Ошибка при получении квот RAG: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сервера: {e}"
        )


@router.post("/admin/api/ai-worker/rag/quotas", dependencies=[Depends(verify_admin_jwt)])
async def update_rag_quotas(payload: RAGQuotasUpdate):
    """
    Обновляет настройки квот RAG в Redis.
    """
    try:
        r = get_redis_client()
        await r.set("config:rag_filter_id", str(payload.filter_id))
        await r.set("config:rag_global_quotas", json.dumps(payload.global_quotas))
        await r.set("config:rag_service_quotas", json.dumps(payload.service_quotas))
        
        logger.info("Настройки квот RAG обновлены администратором")
        return {"status": "success"}
    except Exception as e:
        logger.exception("Ошибка при сохранении квот RAG: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сервера: {e}"
        )




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
