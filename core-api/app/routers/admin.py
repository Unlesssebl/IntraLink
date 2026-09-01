import asyncio
import contextlib
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

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


@router.post("/admin/api/printers/rebuild-index", dependencies=[Depends(verify_admin_jwt)])
async def trigger_rebuild_index():
    """
    Публикует событие в Redis для запуска синхронизации и индексации драйверов на воркере.
    """
    try:
        r = get_redis_client()
        event = {
            "event_type": "rebuild_index",
            "tg_user_id": 0,
        }
        await r.publish("printer_actions", json.dumps(event))
        logger.info("Отправлена команда на перестройку индекса драйверов из веб-панели")
        return {"status": "success", "message": "Процесс индексации драйверов запущен в фоновом режиме."}
    except Exception as e:
        logger.exception("Ошибка публикации команды rebuild_index в Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось запустить индексацию: {e}",
        ) from e


@router.post("/admin/api/printers/fast-reindex", dependencies=[Depends(verify_admin_jwt)])
async def trigger_fast_reindex():
    """
    Быстрая переиндексация: читает только extracted-drv-inf (секунды).
    Использовать после ручного добавления папки с драйвером в extracted-drv-inf.
    """
    try:
        r = get_redis_client()
        event = {
            "event_type": "fast_reindex",
            "tg_user_id": 0,
        }
        await r.publish("printer_actions", json.dumps(event))
        logger.info("Отправлена команда на быструю переиндексацию из веб-панели")
        return {"status": "success", "message": "Быстрая переиндексация запущена в фоновом режиме."}
    except Exception as e:
        logger.exception("Ошибка публикации команды fast_reindex в Redis: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось запустить быструю переиндексацию: {e}",
        ) from e


@router.get("/admin/api/printers/index-status", dependencies=[Depends(verify_admin_jwt)])
async def get_index_status():
    """
    Возвращает статус фонового процесса индексации драйверов.
    """
    try:
        r = get_redis_client()
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


@router.post(
    "/admin/api/print-jobs/{task_id}/restart", dependencies=[Depends(verify_admin_jwt)]
)
async def restart_print_job(task_id: int, model_key: str | None = None):
    """
    Повторно запускает существующую задачу из истории.
    """
    try:
        r = get_redis_client()
        job_data_str = await r.get(f"printer_job:{task_id}")
        if not job_data_str:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Задача #{task_id} не найдена в кэше."
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


# ===========================================================================
# ===========================================================================
# 🎯 Live Triage Queue Endpoints (Очередь 1-й линии и интерактивный триаж)
# ===========================================================================

class ApplyActionRequest(BaseModel):
    status_id: int
    comment: str
    minutes: int = 10
    executor_ids: str = "8664,10502"
    is_private: bool = False


class BulkApplyItem(BaseModel):
    task_id: int
    status_id: int
    comment: str
    minutes: int = 10
    executor_ids: str = "8664,10502"
    is_private: bool = False


class BulkApplyRequest(BaseModel):
    tasks: list[BulkApplyItem]


DEFAULT_TEMPLATES_CATALOG = {
    "wifi_access": {
        "name": "Предоставление Wi-Fi (WLAN-WORKNET)",
        "status_id": 29,
        "status_name": "Выполнена (29)",
        "expenses": 10,
        "template": (
            "Доступ к беспроводной корпоративной сети WLAN-WORKNET успешно предоставлен. "
            "Используйте логин и пароль от вашей учетной записи на ПК. Инструкцию по подключению приложил. "
            "По всем вопросам вы можете написать ответ в комментариях к этой заявке."
        ),
        "badge_color": "success",
    },
    "redirect_1c": {
        "name": "Редирект ➔ 06. 1C:Предприятие",
        "status_id": 30,
        "status_name": "Отменена (30)",
        "expenses": 5,
        "template": (
            "Заявка отменена, т. к. создана не в подходящем разделе. "
            "Требуется оставить заявку в подходящем разделе: 06. 1C:Предприятие. По вопросам звоните на номер 49-87."
        ),
        "badge_color": "warning",
    },
    "redirect_directum": {
        "name": "Редирект ➔ 05. Directum",
        "status_id": 30,
        "status_name": "Отменена (30)",
        "expenses": 5,
        "template": (
            "Заявка отменена, т. к. создана не в подходящем разделе. "
            "Требуется оставить заявку в подходящем разделе: 05. Directum. По вопросам звоните на номер 49-87."
        ),
        "badge_color": "warning",
    },
    "redirect_security": {
        "name": "Редирект ➔ 08. Информационная безопасность",
        "status_id": 30,
        "status_name": "Отменена (30)",
        "expenses": 5,
        "template": (
            "Заявка отменена, т. к. создана не в подходящем разделе. "
            "Требуется оставить заявку в подходящем разделе: 08. Информационная безопасность. По вопросам звоните на номер 49-87."
        ),
        "badge_color": "warning",
    },
    "redirect_printers": {
        "name": "Редирект ➔ 03. Оргтехника",
        "status_id": 30,
        "status_name": "Отменена (30)",
        "expenses": 5,
        "template": (
            "Заявка отменена, т. к. создана не в подходящем разделе. "
            "Требуется оставить заявку в подходящем разделе: 03. Оргтехника. По вопросам звоните на номер 49-87."
        ),
        "badge_color": "warning",
    },
    "hardware_repair": {
        "name": "Обслуживание / Ремонт ПК в 112 каб.",
        "status_id": 48,
        "status_name": "Ожидание устройства (48)",
        "expenses": 10,
        "template": (
            "Приносите системный блок / ноутбук в АБК 3, 112 каб. на диагностику, обслуживание и настройку. "
            "О времени визита вы можете написать в комментариях к этой заявке."
        ),
        "badge_color": "primary",
    },
    "duplicate_task": {
        "name": "Дубликат заявки",
        "status_id": 30,
        "status_name": "Отменена (30)",
        "expenses": 5,
        "template": (
            "Заявка отменена как повторная (дубликат ранее созданного инцидента). "
            "Все работы и переписка ведутся в основной заявке. По вопросам звоните на номер 49-87."
        ),
        "badge_color": "warning",
    },
    "pc_offline": {
        "name": "Не вижу ПК в сети",
        "status_id": 35,
        "status_name": "Требует уточнения (35)",
        "expenses": 5,
        "template": (
            "Не вижу ПК в сети.\n"
            "1. Убедитесь в корректности имени ПК;\n"
            "2. Перезагрузите компьютер;\n"
            "3. Проверьте подключение сетевого кабеля.\n"
            "Пожалуйста, напишите в комментариях к заявке, когда ПК будет включен и доступен в сети."
        ),
        "badge_color": "info",
    },
    "printer_offline": {
        "name": "Не вижу МФУ в сети",
        "status_id": 35,
        "status_name": "Требует уточнения (35)",
        "expenses": 5,
        "template": (
            "Не вижу МФУ в сети.\n"
            "1. Убедитесь в корректности имени/IP адреса принтера;\n"
            "2. Перезагрузите МФУ;\n"
            "3. Переподключите сетевой кабель к МФУ.\n"
            "Пожалуйста, напишите в комментариях к заявке о результатах проверки."
        ),
        "badge_color": "info",
    },
    "anydesk_fallback_assistant": {
        "name": "Сбой AnyDesk (Установка Ассистент)",
        "status_id": 35,
        "status_name": "Требует уточнения (35)",
        "expenses": 5,
        "template": (
            "Связь через AnyDesk не устанавливается. Установите программу «Ассистент» по ссылке: https://мойассистент.рф/скачать/\n"
            "После установки укажите в комментарии к этой заявке ваш идентификатор и пароль от программы."
        ),
        "badge_color": "info",
    },
    "file_lock_smb": {
        "name": "Снятие SMB-блокировки файлов",
        "status_id": 27,
        "status_name": "В работе (27)",
        "expenses": 10,
        "template": (
            "Добрый день! Уточните, пожалуйста, в комментариях к этой заявке полный путь к файлу или сетевой папке "
            "для сброса зависшей сессии на файловом сервере."
        ),
        "badge_color": "info",
    },
    "general": {
        "name": "Принятие в работу (1-я линия)",
        "status_id": 27,
        "status_name": "В работе (27)",
        "expenses": 10,
        "template": (
            "Принято в работу специалистом 1-й линии техподдержки. "
            "Пожалуйста, оставайтесь на связи и пишите ответы в комментариях к этой заявке."
        ),
        "badge_color": "secondary",
    },
    "resolved_standard": {
        "name": "Стандартное завершение заявки",
        "status_id": 29,
        "status_name": "Выполнена (29)",
        "expenses": 15,
        "template": (
            "Работы по заявке успешно выполнены. Если возникнут вопросы или потребуется помощь, "
            "пожалуйста, оставьте комментарий в этой заявке."
        ),
        "badge_color": "success",
    },
}


from app.services.template_engine import auto_detect_template, load_templates
from app.services.deduplication import DuplicateDetector


def _get_all_templates() -> dict[str, dict[str, Any]]:
    """
    Возвращает актуальный словарь шаблонов из централизованного template_engine.
    """
    return load_templates()



@router.get("/admin/api/templates", dependencies=[Depends(verify_admin_jwt)])
async def get_templates_catalog():
    """
    Возвращает полный каталог шаблонов ответов заявителю для быстрого выбора в UI.
    """
    templates = _get_all_templates()
    items = []
    for key, data in templates.items():
        items.append({
            "key": key,
            "name": data.get("name", key),
            "status_id": data.get("status_id", 27),
            "status_name": data.get("status_name", "В работе"),
            "expenses": data.get("expenses", 10),
            "template": data.get("template", ""),
            "badge_color": data.get("badge_color", "secondary"),
        })
    return {"templates": items, "map": templates}


# ─── Сетевая экспресс-диагностика хостов ─────────────────────────────────────
_DIAG_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DIAG_CACHE_TTL = 60.0  # 1 минута


async def _check_host_ping_and_ports(host: str) -> dict[str, Any]:
    """
    Выполняет быстрый ICMP-пинг и проверку портов SMB/WinRM.
    """
    import subprocess
    clean_host = host.strip()
    is_win = os.name == "nt"
    timeout_sec = 1.0

    # 1. ICMP Ping
    if is_win:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout_sec * 1000)), clean_host]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", clean_host]

    is_online = False
    avg_rtt = None
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.5)
        out_text = stdout.decode("cp866" if is_win else "utf-8", errors="ignore").lower()
        if proc.returncode == 0 and ("ttl=" in out_text or "bytes=" in out_text or "байт=" in out_text):
            is_online = True
            import re
            rtt_match = re.search(r"(?:время|time)[<=]([0-9\.]+)\s*ms", out_text)
            if not rtt_match:
                rtt_match = re.search(r"(?:среднее|average|avg)[ =]+([0-9\.]+)\s*ms", out_text)
            avg_rtt = f"{rtt_match.group(1)}ms" if rtt_match else "1ms"
    except Exception:
        if proc:
            with contextlib.suppress(Exception):
                proc.kill()

    # 2. Быстрая проверка портов SMB (445) и WinRM (5985)
    smb_ok = False
    winrm_ok = False
    if is_online:
        for port, flag_name in [(445, "smb"), (5985, "winrm")]:
            try:
                conn = asyncio.open_connection(clean_host, port)
                _, writer = await asyncio.wait_for(conn, timeout=0.8)
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                if flag_name == "smb":
                    smb_ok = True
                else:
                    winrm_ok = True
            except Exception:
                pass

    return {
        "host": clean_host,
        "is_online": is_online,
        "avg_rtt": avg_rtt,
        "smb_ok": smb_ok,
        "winrm_ok": winrm_ok,
        "status_label": f"🟢 {avg_rtt}" if is_online else "🔴 Офлайн",
    }


@router.get("/admin/api/diag/{host}", dependencies=[Depends(verify_admin_jwt)])
async def get_host_diagnostics(host: str):
    """
    Возвращает статус доступности рабочего места оператора в реальном времени.
    """
    clean_host = host.strip()
    if not clean_host:
        raise HTTPException(status_code=400, detail="Хост не указан")

    now = time.time()
    if clean_host in _DIAG_CACHE:
        ts, cached_data = _DIAG_CACHE[clean_host]
        if now - ts < _DIAG_CACHE_TTL:
            return cached_data

    result = await _check_host_ping_and_ports(clean_host)
    _DIAG_CACHE[clean_host] = (now, result)
    return result


def _parse_task_custom_fields(data_xml: str | None) -> dict[str, str]:
    if not data_xml:
        return {"pc_name": "", "phone": "", "room": "", "department": ""}
    import re
    res = {"pc_name": "", "phone": "", "room": "", "department": ""}
    matches = re.findall(r'<field id="(\d+)">([^<]*)</field>', data_xml)
    for fid, val in matches:
        v = val.strip()
        if not v:
            continue
        if fid in ("1089", "1112", "1203"):
            res["pc_name"] = v.upper()
        elif fid in ("1088", "1202"):
            res["phone"] = v
        elif fid in ("1087",):
            res["room"] = v
        elif fid in ("1091", "1206"):
            res["department"] = v
    return res


async def _get_service_catalog_map() -> tuple[dict[int, dict], list[dict], dict[int, list[dict]]]:
    """
    Возвращает:
    1. svc_map: словарь всех услуг по ID.
    2. root_services: список корневых сервисов (17 штук).
    3. subservices_by_root: словарь [root_id -> list of child services].
    """
    r = get_redis_client()
    catalog_str = await r.get("worker:service_catalog")
    if not catalog_str:
        with contextlib.suppress(Exception):
            from app.services.worker import sync_service_catalog
            await sync_service_catalog()
            catalog_str = await r.get("worker:service_catalog")

    if not catalog_str:
        return {}, [], {}

    try:
        flat_list = json.loads(catalog_str)
        svc_map = {s["id"]: s for s in flat_list if "id" in s}

        # 1. Выбираем корневые сервисы (ParentId отсутствует или равен None/0)
        root_services = []
        for s in flat_list:
            if not s.get("parent_id") or s.get("parent_id") not in svc_map:
                root_services.append({
                    "id": s["id"],
                    "name": s["name"],
                })

        # 2. Сопоставление дочерних подсервисов с корневыми разделами
        subservices_by_root: dict[int, list[dict]] = {r["id"]: [] for r in root_services}
        for s in flat_list:
            s_id = s["id"]
            curr = s
            visited = set()
            while curr.get("parent_id") and curr.get("parent_id") in svc_map and curr.get("parent_id") not in visited:
                visited.add(curr["id"])
                curr = svc_map[curr.get("parent_id")]
            root_id = curr.get("id")
            if root_id and root_id in subservices_by_root and s_id != root_id:
                subservices_by_root[root_id].append({
                    "id": s_id,
                    "name": s.get("name"),
                    "parent_id": s.get("parent_id"),
                })

        return svc_map, root_services, subservices_by_root
    except Exception as e:
        logger.error("Ошибка парсинга каталога услуг: %s", e)
        return {}, [], {}


def _resolve_service_hierarchy(service_id: int | None, svc_map: dict[int, dict]) -> dict[str, Any]:
    """
    По ServiceId находит точное имя подуслуги, корневой сервис IntraService (1 из 17) и цепочку навигации.
    """
    if not service_id or service_id not in svc_map:
        return {
            "service_id": service_id,
            "service_name": "1-я линия технической поддержки",
            "root_service_id": None,
            "root_service_name": "11. Общие вопросы" if not service_id else "Прочие сервисы",
            "service_path": "1-я линия технической поддержки",
        }

    curr = svc_map[service_id]
    leaf_name = curr.get("name") or "Не указана"
    path_names = [leaf_name]

    visited = set()
    while curr.get("parent_id") and curr.get("parent_id") in svc_map and curr.get("parent_id") not in visited:
        visited.add(curr["id"])
        curr = svc_map[curr.get("parent_id")]
        path_names.append(curr.get("name") or "")

    root_id = curr.get("id")
    root_name = curr.get("name") or leaf_name
    path_names.reverse()

    return {
        "service_id": service_id,
        "service_name": leaf_name,
        "root_service_id": root_id,
        "root_service_name": root_name,
        "service_path": " ➔ ".join(path_names),
    }


def _format_comment(template: str, pc_name: str = "", target_service: str = "", master_task_id: str = "") -> str:
    res = template or ""
    if "{pc_name}" in res:
        res = res.replace("{pc_name}", pc_name if pc_name else "вашем ПК")
    if "{target_service}" in res:
        res = res.replace("{target_service}", target_service if target_service else "соответствующий раздел")
    if "{master_task_id}" in res:
        res = res.replace("{master_task_id}", master_task_id)
    return res


def _classify_queue_task(task: dict[str, Any], svc_info: dict[str, Any] | None = None, pc_name: str = "") -> dict[str, Any]:
    decision = auto_detect_template(task)
    fallback_svc = (svc_info.get("root_service_name") if svc_info else None) or task.get("ServiceName") or "1-я линия техподдержки"
    
    t_key = decision.get("template_key", "general")
    rule_type = t_key
    if rule_type == "wifi_access":
        rule_type = "wlan_access"
    elif rule_type == "wrong_service":
        target_root = decision.get("target_root")
        if target_root == "06":
            rule_type = "redirect_1c"
        elif target_root == "05":
            rule_type = "redirect_directum"
        elif target_root == "08":
            rule_type = "redirect_security"
        elif target_root == "03":
            rule_type = "redirect_printers"
        else:
            rule_type = f"redirect_{target_root}" if target_root else "wrong_service"

    st_id = decision.get("status_id", 27)
    return {
        "rule_type": rule_type,
        "template_key": t_key,
        "category_label": decision.get("name", "1-я линия техподдержки"),
        "ai_summary": decision.get("name", "1-я линия техподдержки"),
        "target_service_name": decision.get("target_service_name") or fallback_svc,
        "is_redirect": decision.get("is_redirect", False) or st_id == 30,
        "has_ai_solution": st_id in (29, 30, 48),
        "score": 10 if (decision.get("is_redirect") or st_id in (29, 30)) else 7,
        "target_status_id": st_id,
        "target_status_name": decision.get("status_name", "В работе"),
        "suggested_comment": decision.get("comment", ""),
        "expenses": decision.get("expenses", 10),
        "badge_color": decision.get("badge_color", "secondary"),
    }


@router.get("/admin/api/queue", dependencies=[Depends(verify_admin_jwt)])
async def get_triage_queue(filter_id: int = 984, limit: int = 50):
    """
    Возвращает открытые заявки очереди 1-й линии с классификацией Rule Engine,
    кастомными полями, шаблонами и предложенными действиями.
    """
    from app.services.crypto import decrypt_token
    from app.services.intraservice import get_tasks

    r = get_redis_client()
    auth_encrypted = await r.get("worker:service_auth_b64")
    if not auth_encrypted:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервисный аккаунт IntraService не инициализирован. Выполните вход через панель администратора.",
        )

    auth_b64 = decrypt_token(auth_encrypted)
    params = {
        "filterid": str(filter_id),
        "include": "status,customfields,service,comments,attachments",
        "pagesize": str(limit),
        "page": "1",
    }

    raw_res = await get_tasks(auth_b64=auth_b64, filters=params)
    tasks = []
    if isinstance(raw_res, dict):
        tasks = raw_res.get("Tasks", [])
    elif isinstance(raw_res, list):
        tasks = raw_res

    svc_map, root_services, subservices_by_root = await _get_service_catalog_map()

    # Детекция дубликатов
    detector = DuplicateDetector()
    duplicates = detector.find_duplicates(tasks)
    dup_map = {d["duplicate_task_id"]: d for d in duplicates}

    items = []
    for t in tasks:
        t_id = t.get("Id")
        if not t_id:
            continue
        c_fields = _parse_task_custom_fields(t.get("Data"))
        svc_info = _resolve_service_hierarchy(t.get("ServiceId"), svc_map)
        cls_info = _classify_queue_task(t, svc_info, pc_name=c_fields.get("pc_name", ""))
        has_ai = cls_info.get("has_ai_solution", False)

        # Формируем список вложений
        attachments = []
        raw_files = t.get("Attachments") or t.get("Files") or []
        if isinstance(raw_files, list):
            for f in raw_files:
                f_id = f.get("Id")
                attachments.append({
                    "id": f_id,
                    "name": f.get("Name") or f.get("FileName") or "Вложение",
                    "size": f.get("Size") or f.get("Length"),
                    "content_type": f.get("ContentType") or "",
                    "url": f.get("Url") or f.get("DownloadUrl") or f"/admin/api/attachments/{f_id}",
                })

        is_dup = t_id in dup_map
        dup_info = dup_map.get(t_id)

        items.append({
            "id": t_id,
            "name": t.get("Name") or "Без темы",
            "description": t.get("Description") or "",
            "ai_summary": cls_info.get("ai_summary", ""),
            "creator": t.get("Creator") or t.get("CreatorLogin") or "Пользователь",
            "creator_login": t.get("CreatorLogin") or "",
            "created": t.get("Created") or "",
            "service_id": svc_info["service_id"],
            "service_name": svc_info["service_name"],
            "root_service_id": svc_info["root_service_id"],
            "root_service_name": svc_info["root_service_name"],
            "service_path": svc_info["service_path"],
            "target_service_name": cls_info.get("target_service_name") or svc_info["root_service_name"],
            "is_redirect": cls_info.get("is_redirect", False),
            "has_ai_solution": has_ai,
            "status_id": t.get("StatusId"),
            "status_name": t.get("StatusName") or "Открыта",
            "pc_name": c_fields["pc_name"],
            "phone": c_fields["phone"],
            "room": c_fields["room"],
            "department": c_fields["department"],
            "rule_type": cls_info["rule_type"],
            "template_key": cls_info.get("template_key", "general"),
            "category_label": cls_info["category_label"],
            "score": cls_info["score"],
            "target_status_id": cls_info["target_status_id"],
            "target_status_name": cls_info["target_status_name"],
            "suggested_comment": cls_info["suggested_comment"],
            "original_comment": cls_info["suggested_comment"],
            "expenses": cls_info.get("expenses", 10),
            "is_private": False,
            "badge_color": cls_info["badge_color"],
            "has_attachments": len(attachments) > 0,
            "attachments": attachments,
            "is_duplicate": is_dup,
            "duplicate_info": dup_info,
        })

    return {
        "total": len(items),
        "filter_id": filter_id,
        "root_services": root_services,
        "subservices_by_root": subservices_by_root,
        "tasks": items,
        "duplicates": duplicates[:10],
    }



@router.get("/admin/api/tasks/{task_id}/details", dependencies=[Depends(verify_admin_jwt)])
async def get_task_details(task_id: int):
    """
    Возвращает расширенные детали задачи: историю комментариев, вложения и кастомные поля.
    """
    from app.services.crypto import decrypt_token
    from app.services.intraservice import get_single_task, get_task_lifetime

    r = get_redis_client()
    auth_encrypted = await r.get("worker:service_auth_b64")
    if not auth_encrypted:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервисный аккаунт не настроен",
        )

    auth_b64 = decrypt_token(auth_encrypted)
    task_data = await get_single_task(auth_b64, task_id)
    if not task_data:
        raise HTTPException(status_code=404, detail=f"Заявка #{task_id} не найдена")

    lifetime = await get_task_lifetime(auth_b64, task_id) or []
    
    # Формируем список комментариев
    comments = []
    for item in lifetime:
        if item.get("Comment"):
            comments.append({
                "id": item.get("Id"),
                "author": item.get("UserName") or item.get("UserLogin") or "Пользователь",
                "created": item.get("Created"),
                "text": item.get("Comment"),
                "is_private": item.get("IsPrivateComment", False),
            })

    # Извлекаем вложения
    attachments = []
    raw_files = task_data.get("Attachments") or task_data.get("Files") or []
    if isinstance(raw_files, list):
        for f in raw_files:
            attachments.append({
                "id": f.get("Id"),
                "name": f.get("Name") or f.get("FileName") or "Вложение",
                "size": f.get("Size") or f.get("Length"),
                "content_type": f.get("ContentType") or "",
                "url": f.get("Url") or f.get("DownloadUrl") or f"/admin/api/attachments/{f.get('Id')}",
            })

    svc_map, _, _ = await _get_service_catalog_map()
    svc_info = _resolve_service_hierarchy(task_data.get("ServiceId"), svc_map)
    c_fields = _parse_task_custom_fields(task_data.get("Data"))
    cls_info = _classify_queue_task(task_data, svc_info, pc_name=c_fields.get("pc_name", ""))

    return {
        "id": task_id,
        "name": task_data.get("Name") or "Без темы",
        "description": task_data.get("Description") or "",
        "ai_summary": cls_info.get("ai_summary", ""),
        "creator": task_data.get("Creator") or task_data.get("CreatorLogin") or "Пользователь",
        "creator_login": task_data.get("CreatorLogin") or "",
        "created": task_data.get("Created") or "",
        "service_id": svc_info["service_id"],
        "service_name": svc_info["service_name"],
        "root_service_id": svc_info["root_service_id"],
        "root_service_name": svc_info["root_service_name"],
        "service_path": svc_info["service_path"],
        "status_id": task_data.get("StatusId"),
        "status_name": task_data.get("StatusName") or "",
        "pc_name": c_fields["pc_name"],
        "phone": c_fields["phone"],
        "room": c_fields["room"],
        "department": c_fields["department"],
        "comments": comments,
        "attachments": attachments,
        "cls_info": cls_info,
    }


@router.post("/admin/api/tasks/{task_id}/apply", dependencies=[Depends(verify_admin_jwt)])
async def apply_task_action(task_id: int, payload: ApplyActionRequest):
    """
    Интерактивно применяет действие к заявке (перевод в статус 27/29/30/48, комментарий, трудозатраты).
    """
    from app.services.crypto import decrypt_token
    from app.services.intraservice import (
        get_single_task,
        update_task_full,
        add_task_expenses,
    )

    r = get_redis_client()
    auth_encrypted = await r.get("worker:service_auth_b64")
    if not auth_encrypted:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервисный аккаунт не настроен",
        )

    auth_b64 = decrypt_token(auth_encrypted)

    # 1. Pre-flight: проверяем статус в живой базе
    task_curr = await get_single_task(auth_b64, task_id)
    if task_curr:
        curr_status = task_curr.get("StatusId")
        if curr_status in (29, 30):
            return {
                "success": True,
                "already_closed": True,
                "task_id": task_id,
                "message": f"Заявка #{task_id} уже закрыта со статусом {curr_status}",
            }

    # 2. Двухэтапный жизненный цикл: переводим в 27 В работе с назначением исполнителей
    await update_task_full(
        auth_b64,
        task_id=task_id,
        status_id=27,
        executor_ids=payload.executor_ids,
    )

    # 3. Списываем трудозатраты
    if payload.minutes > 0:
        await add_task_expenses(auth_b64, task_id=task_id, minutes=payload.minutes)

    # 4. Переводим в целевой статус с комментарием
    ok = await update_task_full(
        auth_b64,
        task_id=task_id,
        status_id=payload.status_id,
        comment=payload.comment.strip(),
        executor_ids=payload.executor_ids,
        is_private=payload.is_private,
    )

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось обновить статус заявки #{task_id} в IntraService",
        )

    logger.info("Заявка #%s успешно обновлена администратором в статус %s", task_id, payload.status_id)
    return {
        "success": True,
        "task_id": task_id,
        "final_status_id": payload.status_id,
        "message": f"Заявка #{task_id} переведена в статус {payload.status_id}",
    }


@router.post("/admin/api/tasks/bulk-apply", dependencies=[Depends(verify_admin_jwt)])
async def bulk_apply_tasks(payload: BulkApplyRequest):
    """
    Пакетно применяет действия к списку выбранных заявок с фиксацией прогресса.
    """
    applied = []
    failed = []

    for item in payload.tasks:
        try:
            req = ApplyActionRequest(
                status_id=item.status_id,
                comment=item.comment,
                minutes=item.minutes,
                executor_ids=item.executor_ids,
                is_private=item.is_private,
            )
            res = await apply_task_action(item.task_id, req)
            applied.append({"task_id": item.task_id, "res": res})
        except Exception as e:
            logger.error("Ошибка пакетного применения к задаче #%d: %s", item.task_id, e)
            failed.append({"task_id": item.task_id, "error": str(e)})

    return {
        "total": len(payload.tasks),
        "success_count": len(applied),
        "failed_count": len(failed),
        "applied": applied,
        "failed": failed,
    }


