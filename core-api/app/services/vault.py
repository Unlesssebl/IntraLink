"""
Единое хранилище секретов и учетных данных инфраструктуры (SSOT Credentials Vault).
Обеспечивает шифрование Fernet в PostgreSQL (system_settings), синхронизацию
и авто-прогрев токенов в Redis (worker:domain_auth, worker:service_auth_b64).
"""

import base64
import json
import logging
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import AutopilotSetting, SystemSetting, TicketRun
from app.services.active_directory import (
    ConnectionTestResult,
    LDAPSConfig,
    test_ldaps_connection,
)
from app.services.crypto import decrypt_token, encrypt_token
from app.services.worker import get_redis_client
from shared.diagnostics import check_tcp_port

logger = logging.getLogger("core_api.vault")

# Ключи настроек в PostgreSQL system_settings
KEY_SERVICE_ACCOUNT = "service_account_config"
KEY_DOMAIN = "domain_config"
KEY_LDAPS_LEGACY = "ldaps_config"
KEY_LOCAL_ADMIN = "local_admin_config"

# Ключи кэша в Redis
REDIS_KEY_SERVICE_AUTH = "worker:service_auth_b64"
REDIS_KEY_SERVICE_USER_ID = "worker:service_user_id"
REDIS_KEY_DOMAIN_AUTH = "worker:domain_auth"
REDIS_KEY_WIN_DAEMON_HEALTH = "worker:health:win_daemon"


async def get_raw_setting(db: AsyncSession, key: str) -> dict[str, Any] | None:
    """Вычитывает JSON-конфигурацию настройки по ключу из PostgreSQL."""
    stmt = select(SystemSetting).where(SystemSetting.key == key)
    res = await db.execute(stmt)
    setting = res.scalar_one_or_none()
    return setting.value_json if setting else None


async def set_raw_setting(
    db: AsyncSession,
    key: str,
    value_json: dict[str, Any],
    is_encrypted: bool = True,
    description: str | None = None,
) -> None:
    """Сохраняет JSON-конфигурацию настройки в PostgreSQL."""
    stmt = select(SystemSetting).where(SystemSetting.key == key)
    res = await db.execute(stmt)
    setting = res.scalar_one_or_none()
    if setting:
        setting.value_json = value_json
        setting.is_encrypted = is_encrypted
        if description:
            setting.description = description
    else:
        setting = SystemSetting(
            key=key,
            value_json=value_json,
            is_encrypted=is_encrypted,
            description=description,
        )
        db.add(setting)
    await db.commit()


async def sync_vault_to_redis(db: AsyncSession) -> dict[str, bool]:
    """
    Синхронизирует и прогревает секреты из PostgreSQL (system_settings) в Redis.
    Вызывается при старте FastAPI (lifespan) и после каждого сохранения учетных записей.
    """
    results = {"service_auth": False, "service_identity": False, "domain_auth": False}
    try:
        r = get_redis_client()
    except Exception as e:
        logger.error("Не удалось подключиться к Redis для синхронизации Vault: %s", e)
        return results

    # 1. Синхронизация сервисного аккаунта IntraService
    try:
        service_cfg = await get_raw_setting(db, KEY_SERVICE_ACCOUNT)
        if service_cfg and service_cfg.get("login") and service_cfg.get("encrypted_password"):
            pwd = decrypt_token(service_cfg["encrypted_password"])
            login = service_cfg["login"].strip()
            auth_plain = f"{login}:{pwd}"
            auth_b64 = base64.b64encode(auth_plain.encode("utf-8")).decode("ascii")
            encrypted_b64 = encrypt_token(auth_b64)
            await r.set(REDIS_KEY_SERVICE_AUTH, encrypted_b64)
            if service_cfg.get("user_id") is not None:
                await r.set(REDIS_KEY_SERVICE_USER_ID, str(int(service_cfg["user_id"])))
                results["service_identity"] = True
            results["service_auth"] = True
            logger.info("Vault: Учетные данные IntraService синхронизированы в Redis (%s)", REDIS_KEY_SERVICE_AUTH)
        else:
            await r.delete(REDIS_KEY_SERVICE_AUTH, REDIS_KEY_SERVICE_USER_ID)
    except Exception as e:
        logger.exception("Vault: Ошибка синхронизации сервисного аккаунта IntraService в Redis: %s", e)

    # 2. Синхронизация доменной учетной записи (WinRM + LDAPS)
    try:
        domain_cfg = await get_raw_setting(db, KEY_DOMAIN)
        # Fallback на legacy ldaps_config если domain_config еще не сохранен
        if not domain_cfg:
            domain_cfg = await get_raw_setting(db, KEY_LDAPS_LEGACY)

        if domain_cfg:
            username = domain_cfg.get("username") or domain_cfg.get("user_dn")
            enc_pwd = domain_cfg.get("encrypted_password")
            if username and enc_pwd:
                pwd = decrypt_token(enc_pwd)
                auth_data = {"username": username.strip(), "password": pwd}
                encrypted_domain = encrypt_token(json.dumps(auth_data))
                await r.set(REDIS_KEY_DOMAIN_AUTH, encrypted_domain)
                results["domain_auth"] = True
                logger.info("Vault: Доменная учетная запись синхронизирована в Redis (%s)", REDIS_KEY_DOMAIN_AUTH)
    except Exception as e:
        logger.exception("Vault: Ошибка синхронизации доменной учетной записи в Redis: %s", e)

    return results


async def get_vault_status(db: AsyncSession) -> dict[str, Any]:
    """
    Возвращает сводный статус готовности всех инфраструктурных доступов
    без раскрытия паролей.
    """
    # 1. IntraService
    service_cfg = await get_raw_setting(db, KEY_SERVICE_ACCOUNT) or {}
    has_service_acc = bool(
        service_cfg.get("login")
        and service_cfg.get("encrypted_password")
        and service_cfg.get("user_id") is not None
    )
    service_login = service_cfg.get("login")
    service_user_id = service_cfg.get("user_id")

    # 2. Доменная учетная запись (WinRM + LDAPS)
    domain_cfg = await get_raw_setting(db, KEY_DOMAIN)
    if not domain_cfg:
        domain_cfg = await get_raw_setting(db, KEY_LDAPS_LEGACY) or {}
    has_domain = bool(
        (domain_cfg.get("username") or domain_cfg.get("user_dn"))
        and domain_cfg.get("encrypted_password")
    )
    domain_user = domain_cfg.get("username") or domain_cfg.get("user_dn")
    domain_name = domain_cfg.get("domain") or domain_cfg.get("domain_name") or settings.AD_DOMAIN_NAME
    dc_host = domain_cfg.get("dc_host") or domain_cfg.get("server") or domain_name
    base_dn = domain_cfg.get("base_dn") or f"DC={domain_name.replace('.', ',DC=')}"
    wlan_group = domain_cfg.get("wlan_group_name") or settings.AD_WLAN_GROUP_NAME

    # 3. Локальный администратор
    local_cfg = await get_raw_setting(db, KEY_LOCAL_ADMIN) or {}
    has_local_admin = bool(local_cfg.get("username") and local_cfg.get("encrypted_password"))
    local_user = local_cfg.get("username") or ".\\Администратор"

    # 4. Redis статус синхронизации
    redis_service_synced = False
    redis_identity_synced = False
    redis_domain_synced = False
    worker_daemon_online = False

    try:
        r = get_redis_client()
        redis_service_synced = bool(await r.get(REDIS_KEY_SERVICE_AUTH))
        redis_identity_synced = bool(await r.get(REDIS_KEY_SERVICE_USER_ID))
        redis_domain_synced = bool(await r.get(REDIS_KEY_DOMAIN_AUTH))
        worker_status = await r.get(REDIS_KEY_WIN_DAEMON_HEALTH)
        worker_daemon_online = (worker_status == "online") if isinstance(worker_status, str) else False
    except Exception as e:
        logger.debug("Vault: Не удалось прочесть статусы Redis: %s", e)

    is_all_ready = (
        has_service_acc
        and has_domain
        and redis_service_synced
        and redis_identity_synced
        and redis_domain_synced
    )

    return {
        "is_ready": is_all_ready,
        "service_account": {
            "is_configured": has_service_acc,
            "login": service_login,
            "user_id": service_user_id,
            "redis_synced": redis_service_synced,
            "identity_synced": redis_identity_synced,
            "base_url": service_cfg.get("base_url") or settings.INTRASERVICE_URL,
        },
        "domain": {
            "is_configured": has_domain,
            "username": domain_user,
            "domain": domain_name,
            "dc_host": dc_host,
            "ldaps_port": domain_cfg.get("ldaps_port") or domain_cfg.get("port") or 636,
            "base_dn": base_dn,
            "wlan_group_name": wlan_group,
            "redis_synced": redis_domain_synced,
        },
        "local_admin": {
            "is_configured": has_local_admin,
            "username": local_user,
        },
        "execution_worker": {
            "online": worker_daemon_online,
            "heartbeat_key": REDIS_KEY_WIN_DAEMON_HEALTH,
        },
    }


async def save_service_account_credentials(
    db: AsyncSession,
    login: str,
    password: str | None,
    base_url: str | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """
    Сохраняет сервисный аккаунт IntraService в PostgreSQL с шифрованием Fernet
    и выполняет автоматический прогрев Redis ключа worker:service_auth_b64.
    """
    current_raw = await get_raw_setting(db, KEY_SERVICE_ACCOUNT) or {}
    current_user_id = current_raw.get("user_id")
    if user_id is None:
        if current_raw.get("login") != login.strip() or current_user_id is None:
            raise ValueError("verified_service_user_id_required")
        user_id = int(current_user_id)
    await assert_service_identity_change_allowed(db, int(user_id), current_user_id)
    enc_pwd = current_raw.get("encrypted_password")
    if password and password.strip():
        enc_pwd = encrypt_token(password.strip())

    payload = {
        "login": login.strip(),
        "encrypted_password": enc_pwd,
        "base_url": (base_url or current_raw.get("base_url") or settings.INTRASERVICE_URL).strip(),
        "user_id": int(user_id),
    }

    await set_raw_setting(
        db,
        key=KEY_SERVICE_ACCOUNT,
        value_json=payload,
        is_encrypted=True,
        description="Сервисный аккаунт IntraService (SSOT)",
    )

    # Синхронизация в Redis
    sync_result = await sync_vault_to_redis(db)

    return {
        "status": "success",
        "login": payload["login"],
        "is_password_set": bool(enc_pwd),
        "base_url": payload["base_url"],
        "user_id": payload["user_id"],
        "redis_synced": bool(sync_result.get("service_auth")),
        "identity_synced": bool(sync_result.get("service_identity")),
    }


async def assert_service_identity_change_allowed(
    db: AsyncSession, new_user_id: int | None, current_user_id: Any
) -> None:
    """Allow password rotation, but gate replacement/removal of the service identity."""
    if current_user_id is not None and new_user_id is not None and int(current_user_id) == int(new_user_id):
        return
    setting = await db.get(AutopilotSetting, "global")
    if setting is not None and setting.enabled:
        raise ValueError("autopilot_must_be_disabled_before_service_identity_change")
    active_count = await db.scalar(
        select(func.count(TicketRun.id)).where(TicketRun.completed_at.is_(None))
    )
    if active_count:
        raise ValueError("active_ticket_runs_block_service_identity_change")


async def get_service_account_user_id(
    db: AsyncSession | None = None, *, redis_client: Any | None = None
) -> int | None:
    """Resolve the verified service identity from Redis with PostgreSQL fallback."""
    r = redis_client
    if r is None:
        try:
            r = get_redis_client()
        except Exception:
            r = None
    if r is not None:
        try:
            raw = await r.get(REDIS_KEY_SERVICE_USER_ID)
            if raw is not None:
                return int(raw)
        except Exception:
            pass
    if db is None:
        return None
    config = await get_raw_setting(db, KEY_SERVICE_ACCOUNT) or {}
    raw_id = config.get("user_id")
    if raw_id is None:
        return None
    user_id = int(raw_id)
    if r is not None:
        try:
            await r.set(REDIS_KEY_SERVICE_USER_ID, str(user_id))
        except Exception:
            pass
    return user_id


async def get_service_account_auth_b64(db: AsyncSession) -> str | None:
    """Build the encrypted Basic credential from the PostgreSQL SSOT.

    This intentionally does not depend on Redis so correctness checks remain
    available while the authorization cache is degraded.
    """
    config = await get_raw_setting(db, KEY_SERVICE_ACCOUNT) or {}
    login = str(config.get("login") or "").strip()
    encrypted_password = config.get("encrypted_password")
    if not login or not encrypted_password:
        return None
    password = decrypt_token(encrypted_password)
    encoded = base64.b64encode(f"{login}:{password}".encode("utf-8")).decode("ascii")
    return encrypt_token(encoded)


async def save_domain_credentials(
    db: AsyncSession,
    username: str,
    password: str | None,
    domain: str | None = None,
    dc_host: str | None = None,
    ldaps_port: int = 636,
    base_dn: str | None = None,
    wlan_group: str | None = None,
) -> dict[str, Any]:
    """
    Сохраняет единую доменную учетную запись (WinRM + LDAPS) с шифрованием Fernet
    и синхронизирует токен в Redis (worker:domain_auth).
    """
    current_raw = await get_raw_setting(db, KEY_DOMAIN) or await get_raw_setting(db, KEY_LDAPS_LEGACY) or {}

    enc_pwd = current_raw.get("encrypted_password")
    if password and password.strip():
        enc_pwd = encrypt_token(password.strip())

    effective_domain = (domain or current_raw.get("domain") or current_raw.get("domain_name") or settings.AD_DOMAIN_NAME).strip()
    effective_dc = (dc_host or current_raw.get("dc_host") or current_raw.get("server") or effective_domain).strip()
    effective_base_dn = (base_dn or current_raw.get("base_dn") or f"DC={effective_domain.replace('.', ',DC=')}").strip()
    effective_wlan = (wlan_group or current_raw.get("wlan_group_name") or settings.AD_WLAN_GROUP_NAME).strip()

    payload = {
        "username": username.strip(),
        "user_dn": username.strip(),
        "encrypted_password": enc_pwd,
        "domain": effective_domain,
        "domain_name": effective_domain,
        "dc_host": effective_dc,
        "server": effective_dc,
        "ldaps_port": ldaps_port,
        "port": ldaps_port,
        "base_dn": effective_base_dn,
        "wlan_group_name": effective_wlan,
    }

    # Сохраняем в primary domain_config и legacy ldaps_config для 100% совместимости
    await set_raw_setting(
        db,
        key=KEY_DOMAIN,
        value_json=payload,
        is_encrypted=True,
        description="Единая доменная учетная запись (WinRM + LDAPS SSOT)",
    )
    await set_raw_setting(
        db,
        key=KEY_LDAPS_LEGACY,
        value_json=payload,
        is_encrypted=True,
        description="Параметры подключения к Active Directory по LDAPS",
    )

    # Синхронизация в Redis
    await sync_vault_to_redis(db)

    return {
        "status": "success",
        "username": payload["username"],
        "domain": payload["domain"],
        "dc_host": payload["dc_host"],
        "ldaps_port": payload["ldaps_port"],
        "is_password_set": bool(enc_pwd),
    }


async def save_local_admin_credentials(
    db: AsyncSession,
    username: str,
    password: str | None,
) -> dict[str, Any]:
    """
    Сохраняет учетные данные локального администратора с шифрованием Fernet.
    """
    current_raw = await get_raw_setting(db, KEY_LOCAL_ADMIN) or {}

    enc_pwd = current_raw.get("encrypted_password")
    if password and password.strip():
        enc_pwd = encrypt_token(password.strip())

    payload = {
        "username": username.strip(),
        "encrypted_password": enc_pwd,
    }

    await set_raw_setting(
        db,
        key=KEY_LOCAL_ADMIN,
        value_json=payload,
        is_encrypted=True,
        description="Учетная запись локального администратора (резервный fallback)",
    )

    return {
        "status": "success",
        "username": payload["username"],
        "is_password_set": bool(enc_pwd),
    }


async def test_winrm_connection(
    target_host: str,
    port: int = 5985,
    timeout_sec: float = 2.0,
) -> dict[str, Any]:
    """
    Моментальная экспресс-проверка доступности порта WinRM (5985) на целевом хосте.
    """
    start_time = time.perf_counter()
    is_open = await check_tcp_port(target_host, port, timeout=timeout_sec)
    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

    if is_open:
        return {
            "success": True,
            "host": target_host,
            "port": port,
            "latency_ms": elapsed_ms,
            "message": f"Порт WinRM {port} на хосте {target_host} успешно доступен ({elapsed_ms} мс).",
        }
    return {
        "success": False,
        "host": target_host,
        "port": port,
        "latency_ms": elapsed_ms,
        "message": f"Не удалось подключиться к WinRM порту {port} на хосте {target_host} (таймаут или порт закрыт).",
    }
