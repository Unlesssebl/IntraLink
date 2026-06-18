import asyncio
import contextlib
import json
import logging
import os
import random
import time
from pathlib import Path

from datetime import datetime, timedelta, UTC
import jwt
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from app.routers.deps import verify_admin_jwt
from app.services.worker import get_redis_client
from app.services.intraservice import verify_credentials
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin UI"])

PING_INTERVAL = 15.0


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/admin/api/login")
async def admin_login(payload: LoginRequest, response: Response):
    """
    Проверяет учетные данные администратора в IntraService.
    При успехе сохраняет подписанный JWT токен в HttpOnly Cookie.
    """
    auth_b64, user_id = await verify_credentials(payload.username, payload.password)
    if not auth_b64:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )

    # Сохраняем учетные данные администратора как сервисный аккаунт в Redis
    try:
        from app.services.crypto import encrypt_token

        r = get_redis_client()
        encrypted_auth = encrypt_token(auth_b64)
        await r.set("worker:service_auth_b64", encrypted_auth)
        logger.info(
            "Учетные данные администратора '%s' сохранены в Redis для фонового воркера",
            payload.username,
        )
    except Exception as e:
        logger.error(
            "Не удалось сохранить учетные данные администратора в Redis: %s", e
        )

    expire = datetime.now(UTC) + timedelta(hours=12)
    token_data = {"sub": payload.username, "user_id": user_id, "exp": expire}
    token = jwt.encode(token_data, settings.JWT_SECRET or "", algorithm="HS256")

    response.set_cookie(
        key="admin_session",
        value=token,
        httponly=True,
        max_age=12 * 3600,
        expires=expire,
        samesite="lax",
        secure=False,
    )
    return {"status": "success", "username": payload.username}


@router.post("/admin/api/logout")
async def admin_logout(response: Response):
    """
    Удаляет куку сессии администратора.
    """
    response.delete_cookie(key="admin_session")
    return {"status": "success"}


@router.get("/admin/api/me")
async def admin_me(username: str = Depends(verify_admin_jwt)):
    """
    Возвращает информацию о текущем авторизованном администраторе.
    """
    return {"username": username}


class DomainAuthRequest(BaseModel):
    username: str
    password: str | None = None


@router.post("/admin/api/domain-auth", dependencies=[Depends(verify_admin_jwt)])
async def set_domain_auth(payload: DomainAuthRequest):
    """
    Сохраняет доменную учетную запись (WINRM) в Redis в зашифрованном виде.
    """
    try:
        from app.services.crypto import encrypt_token, decrypt_token

        r = get_redis_client()
        password = payload.password
        if not password:
            encrypted_auth = await r.get("worker:domain_auth")
            if encrypted_auth:
                with contextlib.suppress(Exception):
                    old_auth_json = decrypt_token(encrypted_auth)
                    old_auth_data = json.loads(old_auth_json)
                    password = old_auth_data.get("password")

        auth_data = {"username": payload.username, "password": password or ""}
        auth_json = json.dumps(auth_data)
        encrypted_auth = encrypt_token(auth_json)
        await r.set("worker:domain_auth", encrypted_auth)
        logger.info("Доменная учетная запись обновлена в Redis из веб-панели")
        return {"status": "success"}
    except Exception as e:
        logger.exception("Ошибка при сохранении доменной учетной записи: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось сохранить учетную запись: {e}",
        ) from e


@router.get("/admin/api/domain-auth", dependencies=[Depends(verify_admin_jwt)])
async def get_domain_auth_status():
    """
    Возвращает статус настройки доменной учетной записи.
    Пароль не возвращается в целях безопасности.
    """
    try:
        from app.services.crypto import decrypt_token

        r = get_redis_client()
        encrypted_auth = await r.get("worker:domain_auth")
        if not encrypted_auth:
            return {"is_configured": False, "username": None}

        auth_json = decrypt_token(encrypted_auth)
        auth_data = json.loads(auth_json)
        return {"is_configured": True, "username": auth_data.get("username")}
    except Exception as e:
        logger.exception("Ошибка при чтении доменной учетной записи: %s", e)
        return {"is_configured": False, "username": None}


class SystemConfigUpdate(BaseModel):
    printer_service_ids: list[int]
    rag_filter_id: int


@router.get("/admin/api/system-config", dependencies=[Depends(verify_admin_jwt)])
async def get_system_config():
    """
    Возвращает общие системные настройки из Redis (фильтр RAG, принтеры и т.д.).
    """
    try:
        r = get_redis_client()
        printer_services_str = await r.get("config:printer_service_ids")
        if printer_services_str:
            printer_service_ids = json.loads(printer_services_str)
        else:
            printer_service_ids = settings.PRINTER_SERVICE_IDS

        filter_id_str = await r.get("config:rag_filter_id")
        rag_filter_id = int(filter_id_str) if filter_id_str else 0

        return {
            "printer_service_ids": printer_service_ids,
            "rag_filter_id": rag_filter_id,
        }
    except Exception as e:
        logger.exception("Ошибка получения системной конфигурации: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сервера: {e}",
        )


@router.post("/admin/api/system-config", dependencies=[Depends(verify_admin_jwt)])
async def update_system_config(payload: SystemConfigUpdate):
    """
    Сохраняет общие системные настройки в Redis.
    """
    try:
        r = get_redis_client()
        await r.set(
            "config:printer_service_ids", json.dumps(payload.printer_service_ids)
        )
        await r.set("config:rag_filter_id", str(payload.rag_filter_id))

        logger.info(
            "Системная конфигурация обновлена: printer_services=%s, rag_filter_id=%s",
            payload.printer_service_ids,
            payload.rag_filter_id,
        )
        return {"status": "success"}
    except Exception as e:
        logger.exception("Ошибка сохранения системной конфигурации: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось сохранить: {e}",
        )


# Вспомогательный класс для ручного запуска задачи
class ManualJobRequest(BaseModel):
    target_pc: str
    model_key: str
    connection_type: str
    printer_address: str | None = None


# Путь к файлу базы знаний
KB_PATH = Path(
    os.environ.get(
        "PRINTERS_KB_PATH",
        str(
            Path(__file__).resolve().parent
            / ".."
            / ".."
            / ".."
            / "printer-worker"
            / "knowledge_base"
            / "printers_knowledge_base.json"
        ),
    )
)

# Путь к файлу шаблона админ-панели
HTML_PATH = Path(__file__).resolve().parent / ".." / "static" / "admin" / "index.html"


@router.get("/admin", response_class=HTMLResponse)
async def get_admin_ui():
    """
    Отдает HTML-страницу админ-панели.
    """
    if not HTML_PATH.exists():
        # Если static/admin не существует, отдадим страницу заглушки
        return HTMLResponse(
            content=(
                "<h1>Панель администратора не найдена на сервере.</h1>"
                "<p>Пожалуйста, убедитесь, что static/admin/index.html существует.</p>"
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    try:
        with HTML_PATH.open(encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except Exception as e:
        logger.exception("Ошибка при чтении файла шаблона админ-панели: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сервера при загрузке UI: {e}",
        ) from e


@router.get("/admin/api/worker-status", dependencies=[Depends(verify_admin_jwt)])
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


@router.get("/admin/api/knowledge-base", dependencies=[Depends(verify_admin_jwt)])
async def get_knowledge_base():
    """
    Считывает и отдает базу знаний принтеров.
    """
    if not KB_PATH.exists():  # noqa: ASYNC240
        logger.error("Файл базы знаний не найден по пути: %s", KB_PATH)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл базы знаний не найден на сервере.",
        )
    try:
        with KB_PATH.open(encoding="utf-8") as f:  # noqa: ASYNC230
            return json.load(f)
    except Exception as e:
        logger.exception("Ошибка парсинга базы знаний: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка парсинга JSON базы знаний.",
        ) from e


@router.get("/admin/api/print-jobs", dependencies=[Depends(verify_admin_jwt)])
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
            "printer_address": payload.printer_address.strip()
            if payload.printer_address
            else None,
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


class JobActionRequest(BaseModel):
    action: str


@router.post(
    "/admin/api/print-jobs/{task_id}/action", dependencies=[Depends(verify_admin_jwt)]
)
async def handle_job_action(task_id: int, payload: JobActionRequest):
    """
    Отправляет решение пользователя (approve/reject/ask_user) в воркер.
    """
    try:
        r = get_redis_client()
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


@router.delete(
    "/admin/api/print-jobs/{task_id}", dependencies=[Depends(verify_admin_jwt)]
)
async def delete_print_job(task_id: int):
    """
    Удаляет задачу и её логи из Redis.
    """
    try:
        r = get_redis_client()
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
    r = get_redis_client()
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
    "/admin/api/print-jobs/{job_id}/logs", dependencies=[Depends(verify_admin_jwt)]
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
