import datetime
import logging
import secrets
from typing import Any
import jwt
from fastapi import APIRouter, Depends, HTTPException, Header, Request, Response, status, Cookie
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import SystemSetting, get_db
from app.routers.deps import require_permission
from app.services.active_directory import (
    ConnectionTestResult,
    LDAPSConfig,
    test_ldaps_connection,
)
from app.services.crypto import decrypt_token, encrypt_token
from app.services.intraservice import verify_credentials
from app.services import vault

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-settings"])

JWT_ALGORITHM = "HS256"


# ==========================================
# Pydantic схемы
# ==========================================


class AdminLoginRequest(BaseModel):
    username: str | None = Field(None, description="Логин учетной записи IntraService")
    password: str | None = Field(None, description="Пароль учетной записи IntraService или мастер-пароль")


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


class VaultServiceAccountDTO(BaseModel):
    login: str = Field(..., description="Логин сервисной учетной записи IntraService")
    password: str | None = Field(None, description="Пароль учетной записи")
    base_url: str | None = Field(None, description="Базовый URL IntraService API")


class VaultDomainDTO(BaseModel):
    username: str = Field(..., description="Имя доменного пользователя (UPN, svc_intralink@corp.loc)")
    password: str | None = Field(None, description="Доменный пароль")
    domain: str | None = Field(None, description="Домен (например: corporate.loc)")
    dc_host: str | None = Field(None, description="Контроллер домена или IP")
    ldaps_port: int = Field(636, description="Порт LDAPS (по умолчанию 636)")
    base_dn: str | None = Field(None, description="Базовый DN каталога")
    wlan_group_name: str | None = Field(None, description="Имя группы Wi-Fi")


class VaultLocalAdminDTO(BaseModel):
    username: str = Field(".\\Администратор", description="Имя локального администратора")
    password: str | None = Field(None, description="Пароль локального администратора")


class VaultWinrmTestRequest(BaseModel):
    target_host: str = Field(..., description="Целевой хост или IP для проверки WinRM")
    port: int = Field(5985, description="Порт WinRM (по умолчанию 5985)")
    timeout_sec: float = Field(2.0, description="Таймаут проверки в секундах")


# ==========================================
# Зависимость аутентификации администратора
# ==========================================


async def require_admin_auth(
    context=Depends(require_permission("identity:manage")),
) -> dict[str, Any]:
    """
    Проверяет валидность сессионного JWT-токена администратора.
    Защищает административные маршруты /admin/settings.
    Поддерживает:
    - Заголовок Authorization: Bearer <token>
    - Заголовок X-Admin-Token: <token>
    - HttpOnly Cookie 'admin_session' (Single Sign-On из операторской панели)
    """
    return {
        "sub": context.subject,
        "principal_id": str(context.principal_id) if context.principal_id else None,
        "roles": sorted(context.roles),
    }


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
async def admin_login(
    request: Request,
    response: Response,
    body: AdminLoginRequest | None = None,
    admin_session: str | None = Cookie(None),
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Вход в панель администратора по корпоративным учетным данным IntraService (RBAC).
    Поддерживает:
    1. Передачу username и password (IntraService).
    2. Fallback по сессионному cookie 'admin_session' или Bearer токену, если пользователь уже авторизован.
    3. Fallback по устаревшему ADMIN_PASSWORD (если задан в конфигурации).
    """
    if body and body.username and body.password:
        auth_b64, user_id = await verify_credentials(body.username, body.password)
        if not auth_b64:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль IntraService")
        from app.routers.admin.auth import _set_session_cookies
        from app.services.identity import ensure_human_principal, get_roles, issue_session
        principal = await ensure_human_principal(
            db,
            username=body.username,
            display_name=body.username,
            external_user_id=user_id,
        )
        if "system_admin" not in await get_roles(db, principal.id):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Учетная запись '{body.username}' не имеет прав администратора системы.",
            )
        access_token, refresh_token, expires_in = await issue_session(
            db,
            principal=principal,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        _set_session_cookies(response, access_token, refresh_token, expires_in)
        try:
            from app.services.worker import get_redis_client
            await get_redis_client().set(
                f"admin_auth:{principal.subject}", encrypt_token(auth_b64), ex=8 * 3600
            )
        except Exception as exc:
            logger.warning("Не удалось сохранить делегированный токен оператора: %s", exc)
        return AdminLoginResponse(
            access_token=access_token, expires_in=expires_in
        )

    token_to_verify = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_candidate = authorization[7:].strip()
        if bearer_candidate and bearer_candidate != "sso_session":
            token_to_verify = bearer_candidate
    if not token_to_verify and admin_session:
        token_to_verify = admin_session.strip()

    if token_to_verify:
        from app.services.identity import authenticate_human_token
        try:
            context = await authenticate_human_token(db, token_to_verify)
            if "system_admin" not in context.roles:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "System administrator role required")
            return AdminLoginResponse(
                access_token=token_to_verify,
                expires_in=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
            )
        except HTTPException:
            if not settings.ALLOW_LEGACY_SHARED_KEYS:
                raise

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Требуется корпоративная авторизация или активная сессия администратора.",
    )


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


# ==========================================
# Единый Credentials Vault API (SSOT)
# ==========================================


@router.get("/vault/status")
async def get_vault_status_endpoint(
    _: dict = Depends(require_admin_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Сводная проверка наличия и статуса всех инфраструктурных доступов без раскрытия паролей.
    """
    return await vault.get_vault_status(db)


@router.post("/vault/service-account")
async def save_vault_service_account_endpoint(
    body: VaultServiceAccountDTO,
    _: dict = Depends(require_admin_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Сохранение сервисного аккаунта IntraService в PostgreSQL с шифрованием Fernet
    и авто-прогревом токена в Redis (worker:service_auth_b64).
    """
    if body.password and body.password.strip():
        auth_b64, user_id = await verify_credentials(body.login.strip(), body.password.strip())
        if not auth_b64:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Не удалось авторизовать сервисный аккаунт в IntraService (неверный логин или пароль)",
            )
    return await vault.save_service_account_credentials(
        db,
        login=body.login,
        password=body.password,
        base_url=body.base_url,
    )


@router.post("/vault/domain")
async def save_vault_domain_endpoint(
    body: VaultDomainDTO,
    _: dict = Depends(require_admin_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Сохранение единой доменной учетной записи (WinRM + LDAPS) в PostgreSQL (Fernet)
    с автоматическим прогревом токена в Redis (worker:domain_auth).
    """
    return await vault.save_domain_credentials(
        db,
        username=body.username,
        password=body.password,
        domain=body.domain,
        dc_host=body.dc_host,
        ldaps_port=body.ldaps_port,
        base_dn=body.base_dn,
        wlan_group=body.wlan_group_name,
    )


@router.post("/vault/local-admin")
async def save_vault_local_admin_endpoint(
    body: VaultLocalAdminDTO,
    _: dict = Depends(require_admin_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Сохранение учетных данных резервного локального администратора с шифрованием Fernet.
    """
    return await vault.save_local_admin_credentials(
        db,
        username=body.username,
        password=body.password,
    )


@router.post("/vault/test-ldaps", response_model=ConnectionTestResult)
async def test_vault_ldaps_endpoint(
    body: LdapsSettingDTO | None = None,
    _: dict = Depends(require_admin_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Моментальная проверка соединения с Active Directory по LDAPS (порт 636).
    """
    return await test_ldaps_endpoint(body, _, db)


@router.post("/vault/test-winrm")
async def test_vault_winrm_endpoint(
    body: VaultWinrmTestRequest,
    _: dict = Depends(require_admin_auth),
):
    """
    Моментальная экспресс-проверка доступности порта WinRM (5985).
    """
    return await vault.test_winrm_connection(
        target_host=body.target_host.strip(),
        port=body.port,
        timeout_sec=body.timeout_sec,
    )
