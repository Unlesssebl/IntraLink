"""
Модульный пакет роутеров панели администратора и Web SPA.
Агрегирует специализированные подмодули: auth, spa, printers, diag, templates, queue, system, users.
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
from .printers import (
    KB_PATH,
    PING_INTERVAL,
    JobActionRequest,
    ManualJobRequest,
    _get_historical_logs,
    delete_print_job,
    get_index_status,
    get_knowledge_base,
    get_print_jobs,
    get_worker_status,
    handle_job_action,
    log_stream_generator,
    restart_print_job,
    router as printers_router,
    stream_job_logs,
    trigger_fast_reindex,
    trigger_manual_job,
    trigger_rebuild_index,
)
from .queue import (
    ApplyActionRequest,
    BulkApplyItem,
    BulkApplyRequest,
    _classify_queue_task,
    _format_comment,
    _get_service_catalog_map,
    _parse_task_custom_fields,
    _resolve_service_hierarchy,
    apply_task_action,
    bulk_apply_tasks,
    download_task_attachment,
    get_task_details,
    get_triage_queue,
    open_task_in_intraservice,
    router as queue_router,
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
from .templates import (
    DEFAULT_TEMPLATES_CATALOG,
    _get_all_templates,
    get_templates_catalog,
    router as templates_router,
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

# Включение всех специализированных подмаршрутов
router.include_router(auth_router)
router.include_router(spa_router)
router.include_router(printers_router)
router.include_router(diag_router)
router.include_router(templates_router)
router.include_router(queue_router)
router.include_router(system_router)
router.include_router(users_router)

__all__ = [
    "AddUserRequest",
    "ApplyActionRequest",
    "BulkApplyItem",
    "BulkApplyRequest",
    "DEFAULT_TEMPLATES_CATALOG",
    "DOMAIN_SUFFIX",
    "DomainAuthRequest",
    "HTML_PATH",
    "JobActionRequest",
    "KB_PATH",
    "LoginRequest",
    "ManualJobRequest",
    "PING_INTERVAL",
    "ServiceUserRequest",
    "_DIAG_CACHE",
    "_DIAG_CACHE_TTL",
    "_check_host_ping_and_ports",
    "_check_single_host",
    "_check_tcp_port",
    "_classify_queue_task",
    "_format_comment",
    "_get_all_templates",
    "_get_historical_logs",
    "_get_service_catalog_map",
    "_parse_task_custom_fields",
    "_resolve_host_ip",
    "_resolve_service_hierarchy",
    "add_telegram_user",
    "admin_login",
    "admin_logout",
    "admin_me",
    "apply_task_action",
    "bulk_apply_tasks",
    "delete_print_job",
    "delete_service_user",
    "delete_telegram_user",
    "download_task_attachment",
    "get_admin_ui",
    "get_domain_auth_status",
    "get_host_diagnostics",
    "get_index_status",
    "get_knowledge_base",
    "get_print_jobs",
    "get_redis_client",
    "get_system_status",
    "get_task_details",
    "get_telegram_users",
    "get_templates_catalog",
    "get_triage_queue",
    "get_worker_logs",
    "get_worker_status",
    "handle_job_action",
    "log_stream_generator",
    "open_task_in_intraservice",
    "restart_print_job",
    "restart_worker_endpoint",
    "router",
    "set_domain_auth",
    "set_service_user",
    "stream_job_logs",
    "toggle_telegram_user",
    "trigger_fast_reindex",
    "trigger_manual_job",
    "trigger_rebuild_index",
    "verify_credentials",
]
