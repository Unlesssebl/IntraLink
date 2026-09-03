import contextlib
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from app.database.db import AsyncSessionLocal
from app.routers.deps import verify_admin_jwt
from app.services import intraservice
from app.services.crypto import encrypt_token
from app.services.worker import start_worker, stop_worker

logger = logging.getLogger(__name__)

router = APIRouter()


class ServiceUserRequest(BaseModel):
    login: str
    password: str


@router.get("/admin/api/status", dependencies=[Depends(verify_admin_jwt)])
async def get_system_status():
    """
    Возвращает статус подключения всех систем: IntraService Circuit Breaker, Redis, PostgreSQL.
    """
    import app.routers.admin as admin

    r = admin.get_redis_client()
    redis_ok = False
    with contextlib.suppress(Exception):
        redis_ok = await r.ping()

    service_auth_raw = await r.get("worker:service_auth_b64")
    service_user_configured = bool(service_auth_raw)

    db_ok = False
    try:
        async with AsyncSessionLocal() as db:
            res = await db.execute(text("SELECT 1"))
            db_ok = res.scalar() == 1
    except Exception:
        db_ok = False

    cb_state = "CLOSED"
    if hasattr(intraservice, "_circuit_breaker"):
        cb_state = intraservice._circuit_breaker.state.value

    is_healthy = redis_ok and db_ok and cb_state != "OPEN"

    return {
        "status": (
            "healthy"
            if is_healthy
            else ("degraded" if redis_ok or db_ok else "unhealthy")
        ),
        "intraservice_connected": cb_state != "OPEN",
        "circuit_breaker_state": cb_state,
        "service_user_configured": service_user_configured,
        "redis_connected": redis_ok,
        "db_connected": db_ok,
        "worker_running": True,
        "last_sync_time": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


@router.post("/admin/api/service-user", dependencies=[Depends(verify_admin_jwt)])
async def set_service_user(payload: ServiceUserRequest):
    """
    Проверяет и сохраняет учетные данные сервисного аккаунта IntraService.
    """
    import app.routers.admin as admin
    from app.services import vault

    auth_b64, user_id = await admin.verify_credentials(
        payload.login, payload.password
    )
    if not auth_b64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не удалось авторизовать сервисный аккаунт в IntraService",
        )

    r = admin.get_redis_client()
    encrypted = encrypt_token(auth_b64)
    await r.set("worker:service_auth_b64", encrypted)

    # Сохраняем в PostgreSQL (SSOT)
    try:
        async with AsyncSessionLocal() as db:
            await vault.save_service_account_credentials(
                db, login=payload.login, password=payload.password
            )
    except Exception as e:
        logger.warning("Не удалось сохранить сервисный аккаунт в PostgreSQL: %s", e)

    logger.info("Сервисный аккаунт %s успешно настроен", payload.login)
    return {"status": "success", "login": payload.login, "user_id": user_id}


@router.delete(
    "/admin/api/service-user", dependencies=[Depends(verify_admin_jwt)]
)
async def delete_service_user():
    """
    Удаляет сервисный аккаунт из Redis и PostgreSQL.
    """
    import app.routers.admin as admin
    from app.services.vault import set_raw_setting, KEY_SERVICE_ACCOUNT

    r = admin.get_redis_client()
    await r.delete("worker:service_auth_b64")

    try:
        async with AsyncSessionLocal() as db:
            await set_raw_setting(db, KEY_SERVICE_ACCOUNT, {})
    except Exception as e:
        logger.warning("Не удалось очистить сервисный аккаунт в PostgreSQL: %s", e)

    return {"status": "success"}


@router.post(
    "/admin/api/worker/restart", dependencies=[Depends(verify_admin_jwt)]
)
async def restart_worker_endpoint():
    """
    Инициирует мягкий перезапуск фонового воркера.
    """
    try:
        await stop_worker()
        await start_worker()
        return {"status": "success", "message": "Воркер перезапущен"}
    except Exception as e:
        logger.exception("Ошибка перезапуска воркера: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/api/worker-logs", dependencies=[Depends(verify_admin_jwt)])
async def get_worker_logs():
    """
    Возвращает последние системные логи воркера.
    """
    import app.routers.admin as admin

    r = admin.get_redis_client()
    # Читаем последние события из Redis Streams
    entries = []
    try:
        events = await r.xrevrange("stream:intraservice_events", count=30)
        for msg_id, data in events:
            entries.append({
                "id": msg_id,
                "type": data.get("event_type", "worker_event"),
                "task_id": data.get("task_id"),
                "message": data.get(
                    "text", json.dumps(data, ensure_ascii=False)
                ),
                "timestamp": (
                    data.get("timestamp", msg_id.split("-")[0])
                    if "-" in msg_id
                    else ""
                ),
            })
    except Exception as e:
        logger.error("Ошибка чтения stream:intraservice_events: %s", e)

    return {"total": len(entries), "logs": entries}
