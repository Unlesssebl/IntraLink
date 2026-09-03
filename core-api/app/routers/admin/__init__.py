"""
Модульный пакет роутеров панели администратора и Web SPA.
Агрегирует специализированные подмодули: auth, spa, diag, system, users.
"""

from fastapi import APIRouter

# Импорт сервисов для обратной совместимости с monkeypatch в тестах
from app.services.intraservice import verify_credentials
from app.services.worker import get_redis_client

from .auth import (
    DomainAuthRequest,
    LoginRequest,
    admin_login,
    admin_logout,
    admin_me,
    get_domain_auth_status,
    router as auth_router,
    set_domain_auth,
)
from .diag import (
    _DIAG_CACHE,
    _DIAG_CACHE_TTL,
    DOMAIN_SUFFIX,
    _check_host_ping_and_ports,
    _check_single_host,
    _check_tcp_port,
    _resolve_host_ip,
    get_host_diagnostics,
    router as diag_router,
)
from .spa import (
    HTML_PATH,
    get_admin_ui,
    router as spa_router,
)
from .system import (
    ServiceUserRequest,
    delete_service_user,
    get_system_status,
    get_worker_logs,
    restart_worker_endpoint,
    router as system_router,
    set_service_user,
)
from .users import (
    AddUserRequest,
    add_telegram_user,
    delete_telegram_user,
    get_telegram_users,
    router as users_router,
    toggle_telegram_user,
)

# Корневой роутер Admin UI
router = APIRouter(tags=["Admin UI"])

# Включение специализированных подмаршрутов
router.include_router(auth_router)
router.include_router(spa_router)
router.include_router(diag_router)
router.include_router(system_router)
router.include_router(users_router)

__all__ = [
    "AddUserRequest",
    "DOMAIN_SUFFIX",
    "DomainAuthRequest",
    "HTML_PATH",
    "LoginRequest",
    "ServiceUserRequest",
    "_DIAG_CACHE",
    "_DIAG_CACHE_TTL",
    "_check_host_ping_and_ports",
    "_check_single_host",
    "_check_tcp_port",
    "_resolve_host_ip",
    "add_telegram_user",
    "admin_login",
    "admin_logout",
    "admin_me",
    "delete_service_user",
    "delete_telegram_user",
    "get_admin_ui",
    "get_domain_auth_status",
    "get_host_diagnostics",
    "get_redis_client",
    "get_system_status",
    "get_telegram_users",
    "get_worker_logs",
    "restart_worker_endpoint",
    "router",
    "set_domain_auth",
    "set_service_user",
    "toggle_telegram_user",
    "verify_credentials",
]
