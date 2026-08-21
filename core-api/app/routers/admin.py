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
# 🎯 Live Triage Queue Endpoints (Очередь 1-й линии и интерактивный триаж)
# ===========================================================================

class ApplyActionRequest(BaseModel):
    status_id: int
    comment: str
    minutes: int = 10
    executor_ids: str = "8664,10502"
    is_private: bool = False


QUEUE_TEMPLATES = {
    "redirect_1c": (
        "Заявка отменена, т. к. создана не в подходящем разделе. "
        "Требуется оставить заявку в подходящем разделе: 06. 1C:Предприятие. По вопросам звоните на номер 49-87."
    ),
    "redirect_directum": (
        "Заявка отменена, т. к. создана не в подходящем разделе. "
        "Требуется оставить заявку в подходящем разделе: 05. Directum. По вопросам звоните на номер 49-87."
    ),
    "redirect_security": (
        "Заявка отменена, т. к. создана не в подходящем разделе. "
        "Требуется оставить заявку в подходящем разделе: 08. Информационная безопасность. По вопросам звоните на номер 49-87."
    ),
    "redirect_printers": (
        "Заявка отменена, т. к. создана не в подходящем разделе. "
        "Требуется оставить заявку в подходящем разделе: 03. Оргтехника. По вопросам звоните на номер 49-87."
    ),
    "hardware_repair": (
        "Приносите системный блок / ноутбук в АБК 3, 112 каб. на диагностику, обслуживание и настройку. "
        "О времени визита вы можете написать в комментариях к этой заявке."
    ),
    "wifi_access": (
        "Доступ к беспроводной корпоративной сети WLAN-WORKNET успешно предоставлен. "
        "Инструкция по подключению направлена. По всем вопросам вы можете написать ответ в комментариях к этой заявке."
    ),
    "duplicate_task": (
        "Заявка отменена как повторная (дубликат ранее созданного инцидента). "
        "Все работы и переписка ведутся в основной заявке. По вопросам звоните на номер 49-87."
    ),
    "general": (
        "Принято в работу специалистом 1-й линии техподдержки. "
        "Пожалуйста, оставайтесь на связи и пишите ответы в комментариях к этой заявке."
    ),
}


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


def _classify_queue_task(task: dict[str, Any]) -> dict[str, Any]:
    name = (task.get("Name") or "").strip()
    desc = (task.get("Description") or "").strip()
    s_name = (task.get("ServiceName") or "").strip()
    full_text = f"{name} {desc} {s_name}".lower()

    # 1. Wi-Fi / WLAN
    if any(w in full_text for w in ["wifi", "wi-fi", "вайфай", "вай-фай", "wlan", "беспроводн"]):
        return {
            "rule_type": "wlan_access",
            "category_label": "Wi-Fi (WLAN-WORKNET)",
            "score": 9,
            "target_status_id": 29,
            "target_status_name": "Выполнена (29)",
            "suggested_comment": QUEUE_TEMPLATES["wifi_access"],
            "badge_color": "success",
        }

    # 2. 1C:Предприятие
    if any(w in full_text for w in ["1с", "1c", "зуп", "утп", "erp", "бухгалтерия 8", "унф", "фреш"]) and not any(w in full_text for w in ["принтер", "печать", "зависает"]):
        return {
            "rule_type": "redirect_1c",
            "category_label": "Редирект ➔ 06. 1С",
            "score": 10,
            "target_status_id": 30,
            "target_status_name": "Отменена (30)",
            "suggested_comment": QUEUE_TEMPLATES["redirect_1c"],
            "badge_color": "warning",
        }

    # 3. Directum
    if any(w in full_text for w in ["directum", "директум", "директуме"]):
        return {
            "rule_type": "redirect_directum",
            "category_label": "Редирект ➔ 05. Directum",
            "score": 10,
            "target_status_id": 30,
            "target_status_name": "Отменена (30)",
            "suggested_comment": QUEUE_TEMPLATES["redirect_directum"],
            "badge_color": "warning",
        }

    # 4. Обслуживание ПК, чистка, тормозит
    if any(w in full_text for w in ["тормозит", "зависает", "чистк", "шумит", "пыл", "переустанов", "греется", "не включается", "синий экран", "глючит", "медленно"]):
        return {
            "rule_type": "hardware_repair",
            "category_label": "Ожидание устройства (Ремонт)",
            "score": 9,
            "target_status_id": 48,
            "target_status_name": "Ожидание устройства (48)",
            "suggested_comment": QUEUE_TEMPLATES["hardware_repair"],
            "badge_color": "primary",
        }

    # 5. Сброс пароля / ИБ
    if any(w in full_text for w in ["сброс парол", "забыл парол", "разблокиров", "учетн", "заблокирован"]):
        return {
            "rule_type": "redirect_security",
            "category_label": "Редирект ➔ 08. ИБ",
            "score": 9,
            "target_status_id": 30,
            "target_status_name": "Отменена (30)",
            "suggested_comment": QUEUE_TEMPLATES["redirect_security"],
            "badge_color": "warning",
        }

    # 6. Оргтехника / Принтеры
    if any(w in full_text for w in ["принтер", "мфу", "сканер", "картридж", "замяти", "не печатает"]):
        return {
            "rule_type": "redirect_printers",
            "category_label": "Оргтехника (03)",
            "score": 8,
            "target_status_id": 27,
            "target_status_name": "В работе (27)",
            "suggested_comment": QUEUE_TEMPLATES["redirect_printers"],
            "badge_color": "info",
        }

    # 7. Общее
    return {
        "rule_type": "general",
        "category_label": "1-я линия техподдержки",
        "score": 6,
        "target_status_id": 27,
        "target_status_name": "В работе (27)",
        "suggested_comment": QUEUE_TEMPLATES["general"],
        "badge_color": "secondary",
    }


@router.get("/admin/api/queue", dependencies=[Depends(verify_admin_jwt)])
async def get_triage_queue(filter_id: int = 984, limit: int = 25):
    """
    Возвращает открытые заявки очереди 1-й линии с классификацией Rule Engine,
    кастомными полями и предложенными действиями.
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

    items = []
    for t in tasks:
        t_id = t.get("Id")
        if not t_id:
            continue
        c_fields = _parse_task_custom_fields(t.get("Data"))
        cls_info = _classify_queue_task(t)

        items.append({
            "id": t_id,
            "name": t.get("Name") or "Без темы",
            "description": t.get("Description") or "",
            "creator": t.get("Creator") or t.get("CreatorLogin") or "Пользователь",
            "created": t.get("Created") or "",
            "service_id": t.get("ServiceId"),
            "service_name": t.get("ServiceName") or "Общий сервис",
            "status_id": t.get("StatusId"),
            "status_name": t.get("StatusName") or "Открыта",
            "pc_name": c_fields["pc_name"],
            "phone": c_fields["phone"],
            "room": c_fields["room"],
            "department": c_fields["department"],
            "rule_type": cls_info["rule_type"],
            "category_label": cls_info["category_label"],
            "score": cls_info["score"],
            "target_status_id": cls_info["target_status_id"],
            "target_status_name": cls_info["target_status_name"],
            "suggested_comment": cls_info["suggested_comment"],
            "badge_color": cls_info["badge_color"],
            "has_attachments": bool(t.get("Attachments") or t.get("Files")),
        })

    return {"total": len(items), "filter_id": filter_id, "tasks": items}


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

    # 3. Списываем трудозатраты (10 мин)
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

