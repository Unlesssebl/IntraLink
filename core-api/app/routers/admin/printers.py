import asyncio
import contextlib
import json
import logging
import os
import random
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.routers.deps import verify_admin_jwt

logger = logging.getLogger(__name__)

router = APIRouter()

PING_INTERVAL = 15.0

# Путь к файлу базы знаний
KB_PATH = Path(
    os.environ.get(
        "PRINTERS_KB_PATH",
        str(
            Path(__file__).resolve().parent.parent.parent
            / "knowledge_base"
            / "printers_knowledge_base.json"
        ),
    )
)


class ManualJobRequest(BaseModel):
    target_pc: str
    model_key: str
    connection_type: str
    printer_address: str | None = None


class JobActionRequest(BaseModel):
    action: str


@router.get("/admin/api/worker-status", dependencies=[Depends(verify_admin_jwt)])
async def get_worker_status():
    """
    Возвращает текущий статус printer-worker (online/offline).
    """
    import app.routers.admin as admin

    try:
        r = admin.get_redis_client()
        status_val = await r.get("printer_worker:status")
        return {"status": "online" if status_val == "online" else "offline"}
    except Exception as e:
        logger.exception("Ошибка проверки статуса воркера: %s", e)
        return {"status": "offline", "error": str(e)}


@router.get("/admin/api/knowledge-base", dependencies=[Depends(verify_admin_jwt)])
async def get_knowledge_base():
    """
    Считывает и отдает базу знаний принтеров.
    """
    if not KB_PATH.exists():
        logger.error("Файл базы знаний не найден по пути: %s", KB_PATH)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл базы знаний не найден на сервере.",
        )
    try:
        with KB_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Ошибка парсинга базы знаний: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка парсинга JSON базы знаний.",
        ) from e


@router.post(
    "/admin/api/printers/rebuild-index",
    dependencies=[Depends(verify_admin_jwt)],
)
async def trigger_rebuild_index():
    """
    Публикует событие в Redis для запуска синхронизации и индексации драйверов на воркере.
    """
    import app.routers.admin as admin

    try:
        r = admin.get_redis_client()
        event = {
            "event_type": "rebuild_index",
            "tg_user_id": 0,
        }
        await r.publish("printer_actions", json.dumps(event))
        logger.info("Отправлена команда на перестройку индекса драйверов из веб-панели")
        return {
            "status": "success",
            "message": "Процесс индексации драйверов запущен в фоновом режиме.",
        }
    except Exception as e:
        logger.exception("Ошибка публикации команды rebuild_index в Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось запустить индексацию: {e}",
        ) from e


@router.post(
    "/admin/api/printers/fast-reindex",
    dependencies=[Depends(verify_admin_jwt)],
)
async def trigger_fast_reindex():
    """
    Быстрая переиндексация: читает только extracted-drv-inf (секунды).
    Использовать после ручного добавления папки с драйвером в extracted-drv-inf.
    """
    import app.routers.admin as admin

    try:
        r = admin.get_redis_client()
        event = {
            "event_type": "fast_reindex",
            "tg_user_id": 0,
        }
        await r.publish("printer_actions", json.dumps(event))
        logger.info("Отправлена команда на быструю переиндексацию из веб-панели")
        return {
            "status": "success",
            "message": "Быстрая переиндексация запущена в фоновом режиме.",
        }
    except Exception as e:
        logger.exception("Ошибка публикации команды fast_reindex в Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось запустить быструю переиндексацию: {e}",
        ) from e


@router.get(
    "/admin/api/printers/index-status",
    dependencies=[Depends(verify_admin_jwt)],
)
async def get_index_status():
    """
    Возвращает статус фонового процесса индексации драйверов.
    """
    import app.routers.admin as admin

    try:
        r = admin.get_redis_client()
        status_val = await r.get("indexer:status")
        last_run = await r.get("indexer:last_run")
        last_result_raw = await r.get("indexer:last_result")
        last_result = json.loads(last_result_raw) if last_result_raw else None
        return {
            "is_running": status_val == "running",
            "last_run": float(last_run) if last_run else None,
            "last_result": last_result,
        }
    except Exception as e:
        logger.error("Ошибка при получении статуса индексатора: %s", e)
        return {"is_running": False, "last_run": None, "last_result": None}


@router.get("/admin/api/print-jobs", dependencies=[Depends(verify_admin_jwt)])
async def get_print_jobs():
    """
    Возвращает список недавних задач из Redis.
    """
    import app.routers.admin as admin

    try:
        r = admin.get_redis_client()
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
                    with contextlib.suppress(Exception):
                        jobs.append(json.loads(data))
        return jobs
    except Exception as e:
        logger.exception("Ошибка получения списка задач из Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка Redis: {e}",
        ) from e


@router.post("/admin/api/print-jobs", dependencies=[Depends(verify_admin_jwt)])
async def trigger_manual_job(payload: ManualJobRequest):
    """
    Вручную инициирует установку принтера, отправляя событие в Redis.
    """
    import app.routers.admin as admin

    try:
        r = admin.get_redis_client()
        # Генерируем случайный ID для ручного задания (8 знаков)
        task_id = random.randint(10000000, 99999999)

        event = {
            "event_type": "manual_trigger",
            "task_id": task_id,
            "tg_user_id": 0,
            "target_pc": payload.target_pc.strip(),
            "model_key": payload.model_key,
            "connection_type": payload.connection_type,
            "printer_address": (
                payload.printer_address.strip()
                if payload.printer_address
                else None
            ),
        }

        # Публикуем событие для printer-worker
        await r.publish("printer_actions", json.dumps(event))
        logger.info("Вручную запущена задача #%d для ПК %s", task_id, payload.target_pc)
        return {"status": "success", "task_id": task_id}
    except Exception as e:
        logger.exception("Ошибка публикации ручной задачи в Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось запустить задачу: {e}",
        ) from e


@router.post(
    "/admin/api/print-jobs/{task_id}/action",
    dependencies=[Depends(verify_admin_jwt)],
)
async def handle_job_action(task_id: int, payload: JobActionRequest):
    """
    Отправляет решение пользователя (approve/reject/ask_user) в воркер.
    """
    import app.routers.admin as admin

    try:
        r = admin.get_redis_client()
        event = {
            "event_type": "approval_response",
            "task_id": task_id,
            "action": payload.action,
            "tg_user_id": 0,  # 0 означает запуск из веб-панели
        }
        await r.publish("printer_actions", json.dumps(event))
        logger.info(
            "Отправлено действие '%s' для задачи #%d из веб-панели",
            payload.action,
            task_id,
        )
        return {"status": "success", "task_id": task_id}
    except Exception as e:
        logger.exception("Ошибка публикации действия в Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось отправить действие: {e}",
        ) from e


@router.post(
    "/admin/api/print-jobs/{task_id}/restart",
    dependencies=[Depends(verify_admin_jwt)],
)
async def restart_print_job(task_id: int, model_key: str | None = None):
    """
    Повторно запускает существующую задачу из истории.
    """
    import app.routers.admin as admin

    try:
        r = admin.get_redis_client()
        job_data_str = await r.get(f"printer_job:{task_id}")
        if not job_data_str:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Задача #{task_id} не найдена в кэше.",
            )

        job_data = json.loads(job_data_str)

        # Если передан model_key, обновляем параметры задачи в кэше
        if model_key:
            job_data["model_key"] = model_key
            # Сбрасываем старый driver_info, чтобы воркер переопределил его по новому model_key
            job_data.pop("driver_info", None)
            # Принудительно ставим ручной режим, чтобы воркер сразу устанавливал с этим ключом
            job_data["is_manual"] = True

        is_manual = job_data.get("is_manual", False)

        if is_manual:
            event = {
                "event_type": "manual_trigger",
                "task_id": task_id,
                "tg_user_id": job_data.get("tg_user_id", 0),
                "target_pc": job_data.get("target_pc"),
                "model_key": job_data.get("model_key"),
                "connection_type": job_data.get("connection_type"),
                "printer_address": job_data.get("printer_address"),
            }
        else:
            # Для автоматической заявки эмулируем получение события из ИС
            event = {
                "event_type": "new_task",
                "task_id": task_id,
                "tg_user_id": job_data.get("tg_user_id", 0),
                "is_user_id": 0,
                "is_login": "",
            }

        # Удаляем старый статус завершенности (failed/done), чтобы UI увидел процесс
        job_data["state"] = "probing"
        job_data["error_message"] = ""
        await r.set(f"printer_job:{task_id}", json.dumps(job_data))

        await r.publish("printer_actions", json.dumps(event))
        logger.info(
            "Задача #%d отправлена на перезапуск из веб-панели (model_key=%s)",
            task_id,
            model_key,
        )
        return {"status": "success", "task_id": task_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка перезапуска задачи: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось перезапустить задачу: {e}",
        ) from e


@router.delete(
    "/admin/api/print-jobs/{task_id}", dependencies=[Depends(verify_admin_jwt)]
)
async def delete_print_job(task_id: int):
    """
    Удаляет задачу и её логи из Redis.
    """
    import app.routers.admin as admin

    try:
        r = admin.get_redis_client()
        # Удаляем задачу из sorted set
        await r.zrem("printer_jobs_list", str(task_id))
        # Удаляем саму задачу
        await r.delete(f"printer_job:{task_id}")
        # Удаляем историю логов задачи
        await r.delete(f"printer_job_logs_history:{task_id}")

        logger.info("Задача #%d удалена из Redis через веб-панель", task_id)
        return {"status": "success", "task_id": task_id}
    except Exception as e:
        logger.exception("Ошибка при удалении задачи #%d из Redis: %s", task_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка Redis: {e}",
        ) from e


async def _get_historical_logs(r, job_id: int) -> list[str]:
    """
    Вспомогательная функция для получения истории логов и состояния задачи из Redis.
    """
    logs = []
    try:
        # Ограничим выдачу истории последними 1000 строками во избежание зависания браузера
        history = await r.lrange(f"printer_job_logs_history:{job_id}", -100, -1)
        if history:
            logs.extend(f"event: message\ndata: {log_line}\n\n" for log_line in history)
    except Exception as e:
        logger.error("Ошибка чтения истории логов для задачи #%d: %s", job_id, e)

    job_data_raw = await r.get(f"printer_job:{job_id}")
    if job_data_raw:
        with contextlib.suppress(Exception):
            job_data = json.loads(job_data_raw)
            state = job_data.get("state", "unknown")
            error_message = job_data.get("error_message")
            msg = f"[SYSTEM] Текущее состояние задачи: {state}"
            if error_message:
                msg += f" (Ошибка: {error_message})"
            logs.append(f"event: message\ndata: {msg}\n\n")
    return logs


async def log_stream_generator(job_id: int):
    """
    Генератор для SSE-стрима логов.
    """
    import app.routers.admin as admin

    r = admin.get_redis_client()
    pubsub = r.pubsub()
    await pubsub.subscribe(f"printer_job_logs:{job_id}")

    yield (
        "event: message\n"
        "data: [SYSTEM] Подключение к потоку логов... ожидание воркера.\n\n"
    )

    # Считываем накопленные логи из истории в Redis
    historical_logs = await _get_historical_logs(r, job_id)
    for log_line in historical_logs:
        yield log_line

    last_msg_time = time.monotonic()

    try:
        while True:
            # Считываем из Pub/Sub с таймаутом
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message:
                log_line = message.get("data")
                if log_line:
                    yield f"event: message\ndata: {log_line}\n\n"
                    last_msg_time = time.monotonic()
            else:
                # Проверяем, сколько времени прошло без сообщений
                if time.monotonic() - last_msg_time > PING_INTERVAL:
                    yield ": ping\n\n"
                    last_msg_time = time.monotonic()
                await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        logger.info("SSE клиент отключился от логов задачи #%d", job_id)
    finally:
        await pubsub.unsubscribe(f"printer_job_logs:{job_id}")
        await pubsub.close()


@router.get(
    "/admin/api/print-jobs/{job_id}/logs",
    dependencies=[Depends(verify_admin_jwt)],
)
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
            "X-Accel-Buffering": "no",
        },
    )
