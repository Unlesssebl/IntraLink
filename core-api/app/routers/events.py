"""
Роутер Event Hub: Real-time стриминг событий (SSE) через Redis Pub/Sub.
Обеспечивает живую обратную связь (прогресс, статусы, HITL-запросы подтверждения)
для Web UI, CLI и Telegram-бота без необходимости постоянного polling.
"""

import asyncio
import json
import logging
from typing import AsyncGenerator
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.routers.deps import verify_admin_or_api_key
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.routers.events")

router = APIRouter(
    prefix="/api/v1/events",
    tags=["Event Hub (Real-time SSE Gateway)"],
    dependencies=[Depends(verify_admin_or_api_key)],
)


@router.get("/stream")
async def event_stream(
    job_id: str | None = Query(
        None, description="ID конкретной задачи для фильтрации событий"
    ),
    channel: str = Query(
        "all", description="Канал событий: 'all' (все задачи) или 'progress'"
    ),
    _auth: str = Depends(verify_admin_or_api_key),
):
    """
    Server-Sent Events (SSE) эндпоинт.
    Подписывается на события Redis Pub/Sub и транслирует их подключенным клиентам.
    """
    async def generate_events() -> AsyncGenerator[str, None]:
        r = get_redis_client()
        pubsub = r.pubsub()
        try:
            if job_id:
                channel_name = f"job:{job_id}:events"
                await pubsub.subscribe(channel_name)
                logger.info("SSE клиент подписался на канал %s", channel_name)
            else:
                # Подписка на глобальный канал, обновления задач и паттерны событий
                await pubsub.subscribe("events:all", "channel:task_updates")
                await pubsub.psubscribe("job:*:events", "intraservice_events:*")
                logger.info("SSE клиент подписался на глобальный поток событий")

            # Отправляем директиву retry: 3000 (RFC 8895) и приветственное событие
            init_payload = json.dumps(
                {"event": "connected", "channel": channel, "job_id": job_id},
                ensure_ascii=False,
            )
            yield f"retry: 3000\nevent: connected\ndata: {init_payload}\n\n"


            while True:
                try:
                    # Ожидание сообщения с таймаутом для отправки keep-alive ping
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                        timeout=15.0,
                    )
                    if message and message.get("type") in ("message", "pmessage"):
                        data = message.get("data")
                        if isinstance(data, bytes):
                            data = data.decode("utf-8")
                        
                        # Парсим тип события (event или type)
                        event_type = "message"
                        try:
                            parsed = json.loads(data)
                            if isinstance(parsed, dict):
                                event_type = parsed.get("event") or parsed.get("type") or "message"
                        except Exception:
                            pass

                        yield f"event: {event_type}\ndata: {data}\n\n"

                except asyncio.TimeoutError:
                    # Keep-alive heartbeat для предотвращения разрыва соединения прокси
                    yield ": ping\n\n"
                except asyncio.CancelledError:
                    logger.debug("SSE соединение закрыто клиентом")
                    break

        except Exception as e:
            logger.exception("Ошибка в потоке SSE: %s", e)
            err_payload = json.dumps({"event": "error", "message": str(e)}, ensure_ascii=False)
            yield f"event: error\ndata: {err_payload}\n\n"
        finally:
            try:
                if job_id:
                    await pubsub.unsubscribe(f"job:{job_id}:events")
                else:
                    await pubsub.punsubscribe("job:*:events", "intraservice_events:*")
                    await pubsub.unsubscribe("events:all", "channel:task_updates")
                await pubsub.close()
            except Exception as ex:
                logger.debug("Ошибка закрытия pubsub SSE: %s", ex)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "text/event-stream",
        "X-Accel-Buffering": "no",
    }

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers=headers,
    )
