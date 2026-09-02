import datetime
import logging
import secrets
from typing import Any
import jwt
from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import SystemSetting, get_db
from app.services.active_directory import (
    ConnectionTestResult,
    LDAPSConfig,
    test_ldaps_connection,
)
from app.services.crypto import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-settings"])

JWT_ALGORITHM = "HS256"


# ==========================================
# Pydantic схемы
# ==========================================


class AdminLoginRequest(BaseModel):
    password: str = Field(..., description="Мастер-пароль администратора")


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class LdapsSettingDTO(BaseModel):
    server: str = "dc.corporate.loc"
    port: int = 636
    use_ssl: bool = True
    user_dn: str = "svc_intralink@corporate.loc"
    password: str | None = None
    is_password_set: bool = False
    base_dn: str = "DC=corporate,DC=loc"
    wlan_group_name: str = "WLAN-WORKNET"
    domain_name: str = "corporate.loc"


class HelpdeskSettingDTO(BaseModel):
    primary_executor_id: int = 8664
    default_executor_ids: str = "8664,10502"
    primary_filter_id: int = 984
    timezone: str = "Europe/Moscow"


class LocalAdminSettingDTO(BaseModel):
    username: str = ".\\Администратор"
    password: str | None = None
    is_password_set: bool = False


class AllSettingsResponse(BaseModel):
    ldaps: LdapsSettingDTO
    helpdesk: HelpdeskSettingDTO
    local_admin: LocalAdminSettingDTO


# ==========================================
# Зависимость аутентификации администратора
# ==========================================


async def require_admin_auth(
    authorization: str | None = Header(None, alias="Authorization"),
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """
    Проверяет валидность сессионного JWT-токена администратора.
    Защищает административные маршруты /admin/settings.
    """
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_admin_token:
        token = x_admin_token.strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация администратора",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        secret = settings.ADMIN_JWT_SECRET or settings.JWT_SECRET or "intralink-admin-secret"
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
        if payload.get("role") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав: требуется роль администратора",
            )
        return payload
    except jwt.PyJWTError as e:
        logger.warning("Invalid admin JWT token: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный или просроченный токен сессии",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ==========================================
# Вспомогательные функции чтения/записи БД
# ==========================================


async def get_system_setting(
    db: AsyncSession, key: str
) -> dict[str, Any] | None:
    stmt = select(SystemSetting).where(SystemSetting.key == key)
    res = await db.execute(stmt)
    setting = res.scalar_one_or_none()
    return setting.value_json if setting else None


async def save_system_setting(
    db: AsyncSession,
    key: str,
    value_json: dict[str, Any],
    is_encrypted: bool = False,
    description: str | None = None,
) -> None:
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


# ==========================================
# Маршруты аутентификации и настроек
# ==========================================


@router.post("/auth/login", response_model=AdminLoginResponse)
async def admin_login(body: AdminLoginRequest):
    """
    Вход в панель администратора по мастер-паролю (ADMIN_PASSWORD).
    Использует secrets.compare_digest для защиты от Timing Attacks.
    """
    expected_password = settings.ADMIN_PASSWORD
    if not secrets.compare_digest(body.password.encode("utf-8"), expected_password.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный мастер-пароль администратора",
        )

    expires_in = 8 * 3600  # 8 часов
    secret = settings.ADMIN_JWT_SECRET or settings.JWT_SECRET or "intralink-admin-secret"
    payload = {
        "sub": "admin",
        "role": "admin",
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in),
        "iat": datetime.datetime.now(datetime.timezone.utc),
    }
    token = jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)

    return AdminLoginResponse(access_token=token, expires_in=expires_in)


@router.get("/settings", response_model=AllSettingsResponse)
async def get_all_settings(
    _: dict = Depends(require_admin_auth),
    db: AsyncSession = Depends(get_db),
):
    """Получение всех настроек системы (пароли замаскированы)."""
    # 1. LDAPS настройки
    raw_ldaps = await get_system_setting(db, "ldaps_config") or {}
    ldaps_dto = LdapsSettingDTO(
        server=raw_ldaps.get("server", settings.AD_DOMAIN_NAME),
        port=raw_ldaps.get("port", 636),
        use_ssl=raw_ldaps.get("use_ssl", True),
        user_dn=raw_ldaps.get("user_dn", f"svc_intralink@{settings.AD_DOMAIN_NAME}"),
        password=None,
        is_password_set=bool(raw_ldaps.get("encrypted_password")),
        base_dn=raw_ldaps.get("base_dn", f"DC={settings.AD_DOMAIN_NAME.replace('.', ',DC=')}"),
        wlan_group_name=raw_ldaps.get("wlan_group_name", settings.AD_WLAN_GROUP_NAME),
        domain_name=raw_ldaps.get("domain_name", settings.AD_DOMAIN_NAME),
    )

    # 2. Helpdesk настройки
    raw_helpdesk = await get_system_setting(db, "helpdesk_profiles") or {}
    helpdesk_dto = HelpdeskSettingDTO(
        primary_executor_id=raw_helpdesk.get("primary_executor_id", settings.PRIMARY_EXECUTOR_ID),
        default_executor_ids=raw_helpdesk.get("default_executor_ids", settings.DEFAULT_EXECUTOR_IDS),
        primary_filter_id=raw_helpdesk.get("primary_filter_id", settings.PRIMARY_TRIAGE_FILTER_ID),
        timezone=raw_helpdesk.get("timezone", settings.INTRASERVICE_TZ),
    )

    # 3. Учетная запись локального администратора (Fallback для LiteManager/DameWare)
    raw_local = await get_system_setting(db, "local_admin_config") or {}
    local_admin_dto = LocalAdminSettingDTO(
        username=raw_local.get("username", ".\\Администратор"),
        password=None,
        is_password_set=bool(raw_local.get("encrypted_password")),
    )

    return AllSettingsResponse(ldaps=ldaps_dto, helpdesk=helpdesk_dto, local_admin=local_admin_dto)


@router.post("/settings/ldaps", response_model=LdapsSettingDTO)
async def update_ldaps_settings(
    body: LdapsSettingDTO,
    _: dict = Depends(require_admin_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Сохранение настроек LDAPS.
    Пароль шифруется Fernet перед записью в таблицу system_settings.
    """
    current_raw = await get_system_setting(db, "ldaps_config") or {}

    encrypted_pwd = current_raw.get("encrypted_password")
    if body.password and body.password.strip():
        encrypted_pwd = encrypt_token(body.password.strip())

    payload = {
        "server": body.server.strip(),
        "port": body.port,
        "use_ssl": body.use_ssl,
        "user_dn": body.user_dn.strip(),
        "encrypted_password": encrypted_pwd,
        "base_dn": body.base_dn.strip(),
        "wlan_group_name": body.wlan_group_name.strip(),
        "domain_name": body.domain_name.strip(),
    }

    await save_system_setting(
        db,
        key="ldaps_config",
        value_json=payload,
        is_encrypted=True,
        description="Параметры подключения к Active Directory по LDAPS",
    )

    return LdapsSettingDTO(
        server=payload["server"],
        port=payload["port"],
        use_ssl=payload["use_ssl"],
        user_dn=payload["user_dn"],
        password=None,
        is_password_set=bool(encrypted_pwd),
        base_dn=payload["base_dn"],
        wlan_group_name=payload["wlan_group_name"],
        domain_name=payload["domain_name"],
    )


@router.post("/settings/ldaps/test", response_model=ConnectionTestResult)
async def test_ldaps_endpoint(
    body: LdapsSettingDTO | None = None,
    _: dict = Depends(require_admin_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Тестирование подключения к Active Directory по LDAPS в реальном времени.
    Если body не передан, тестируются сохраненные в базе настройки.
    """
    current_raw = await get_system_setting(db, "ldaps_config") or {}

    server = (body.server if body else current_raw.get("server")) or settings.AD_DOMAIN_NAME
    port = (body.port if body else current_raw.get("port")) or 636
    use_ssl = (body.use_ssl if body else current_raw.get("use_ssl", True))
    user_dn = (body.user_dn if body else current_raw.get("user_dn")) or ""
    base_dn = (body.base_dn if body else current_raw.get("base_dn")) or ""
    wlan_group = (body.wlan_group_name if body else current_raw.get("wlan_group_name")) or "WLAN-WORKNET"
    domain_name = (body.domain_name if body else current_raw.get("domain_name")) or "corporate.loc"

    # Определение пароля
    password = None
    if body and body.password and body.password.strip():
        password = body.password.strip()
    elif current_raw.get("encrypted_password"):
        password = decrypt_token(current_raw["encrypted_password"])

    if not password:
        return ConnectionTestResult(
            success=False,
            latency_ms=0.0,
            message="Пароль сервисной учетной записи не задан",
            details={"error": "missing_password"},
        )

    config = LDAPSConfig(
        server=server,
        port=port,
        use_ssl=use_ssl,
        user_dn=user_dn,
        password=password,
        base_dn=base_dn,
        wlan_group_name=wlan_group,
        domain_name=domain_name,
    )

    return await test_ldaps_connection(config)


@router.post("/settings/helpdesk", response_model=HelpdeskSettingDTO)
async def update_helpdesk_settings(
    body: HelpdeskSettingDTO,
    _: dict = Depends(require_admin_auth),
    db: AsyncSession = Depends(get_db),
):
    """Сохранение профилей инженеров и параметров очереди (вместо хардкода)."""
    payload = {
        "primary_executor_id": body.primary_executor_id,
        "default_executor_ids": body.default_executor_ids.strip(),
        "primary_filter_id": body.primary_filter_id,
        "timezone": body.timezone.strip(),
    }

    await save_system_setting(
        db,
        key="helpdesk_profiles",
        value_json=payload,
        is_encrypted=False,
        description="Профили инженеров и фильтр очереди IntraService",
    )

    return body


@router.post("/settings/local-admin", response_model=LocalAdminSettingDTO)
async def update_local_admin_settings(
    body: LocalAdminSettingDTO,
    _: dict = Depends(require_admin_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Сохранение учетных данных локального администратора с шифрованием Fernet.
    Используется как fallback для авторизации в LiteManager и DameWare.
    """
    current_raw = await get_system_setting(db, "local_admin_config") or {}

    encrypted_pwd = current_raw.get("encrypted_password")
    if body.password and body.password.strip():
        encrypted_pwd = encrypt_token(body.password.strip())

    payload = {
        "username": body.username.strip(),
        "encrypted_password": encrypted_pwd,
    }

    await save_system_setting(
        db,
        key="local_admin_config",
        value_json=payload,
        is_encrypted=True,
        description="Учетная запись локального администратора (fallback для LiteManager/DameWare)",
    )

    return LocalAdminSettingDTO(
        username=payload["username"],
        password=None,
        is_password_set=bool(encrypted_pwd),
    )
