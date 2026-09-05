import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app.config import settings
from app.database.db import CommandOutbox, CommandRecord, AsyncSessionLocal, init_db, verify_schema
from app.routers import (
    admin,
    admin_settings,
    ai,
    auth,
    commands,
    commands_v2,
    desktop,
    events,
    kb_admin,
    rules_admin,
    self_service,
    service_tasks,
    skills_admin,
    tasks,
    triage,
    users,
)
from app.services.ai import ai_hub
from app.services.intraservice import close_session, init_session
from app.services.template_engine import (
    get_templates_from_db,
    seed_templates_if_empty,
    start_rules_invalidation_listener,
)
from app.services.worker import start_worker, stop_worker
from app.services.rollout import rollout_readiness
from app.services.worker import get_redis_client

# Настройка логирования
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Действия при запуске приложения
    logger.info("Инициализация базы данных...")
    try:
        if settings.DATABASE_URL.startswith("sqlite"):
            await init_db()
        else:
            await verify_schema()
        logger.info("База данных успешно инициализирована.")
    except Exception as e:
        logger.exception("Ошибка при инициализации базы данных: %s", e)
        raise

    # Database Seeding и прогрев L1 кэша шаблонов
    try:
        async with AsyncSessionLocal() as session:
            await seed_templates_if_empty(session)
            await get_templates_from_db(session)
        logger.info("L1 кэш шаблонов триажа инициализирован из PostgreSQL.")
    except Exception as e:
        logger.warning("Ошибка инициализации шаблонов триажа: %s", e)

    # Авто-прогрев и синхронизация кэша секретов Vault в Redis
    try:
        from app.services.vault import sync_vault_to_redis
        async with AsyncSessionLocal() as session:
            await sync_vault_to_redis(session)
        logger.info("Vault: кэш учетных данных успешно прогрет в Redis из PostgreSQL.")
    except Exception as e:
        logger.warning("Vault: ошибка авто-прогрева кэша в Redis: %s", e)

    # Запуск Pub/Sub слушателя инвалидации кэша правил
    invalidation_task = asyncio.create_task(
        start_rules_invalidation_listener(settings.REDIS_URL)
    )

    logger.info("Инициализация HTTP-сессии IntraService...")
    try:
        await init_session()
    except Exception as e:
        logger.exception("Ошибка при инициализации HTTP-сессии: %s", e)

    if settings.ENABLE_INTERNAL_SCHEDULER:
        logger.info("Запуск встроенного планировщика APScheduler в Core API...")
        try:
            await start_worker()
        except Exception as e:
            logger.exception("Ошибка при запуске встроенного воркера: %s", e)
    else:
        logger.info(
            "Встроенный планировщик APScheduler отключен (опрос выполняет внешний сервис poller)."
        )

    yield
    # Действия при остановке приложения
    logger.info("Остановка приложения Core API...")
    invalidation_task.cancel()
    if settings.ENABLE_INTERNAL_SCHEDULER:
        try:
            await stop_worker()
        except Exception as e:
            logger.exception("Ошибка при остановке фонового воркера: %s", e)

    logger.info("Закрытие HTTP-сессий IntraService, RAG и AI Hub...")
    try:
        await close_session()
    except Exception as e:
        logger.exception("Ошибка при закрытии HTTP-сессии: %s", e)

    try:
        await ai_hub.close()
    except Exception as e:
        logger.exception("Ошибка при закрытии сессии AI Hub: %s", e)

    try:
        from app.services.rag import close_rag_session

        await close_rag_session()
    except Exception as e:
        logger.exception("Ошибка при закрытии HTTP-сессии RAG: %s", e)


app = FastAPI(
    title="IntraService Core API Gateway",
    description="Микросервис-шлюз для интеграции с API IntraService",
    version="1.0.0",
    lifespan=lifespan,
)

# Разрешение CORS для локальных веб-клиентов и интерфейсов
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Bot-Api-Key"],
)

# Подключение роутеров с единым префиксом версии API v1
app.include_router(auth.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(service_tasks.router, prefix="/api/v1")
app.include_router(triage.router)
app.include_router(rules_admin.router)
app.include_router(ai.router)
app.include_router(commands.router)
app.include_router(commands_v2.router)
app.include_router(commands_v2.policy_router)
app.include_router(desktop.router)
app.include_router(events.router)
app.include_router(admin.router)
app.include_router(admin_settings.router)
app.include_router(kb_admin.router)
app.include_router(skills_admin.router)
app.include_router(self_service.router)

# Статические файлы интерактивной презентации (при наличии)
PRESENTATIONS_DIR = Path("/app/docs/presentations")
if not PRESENTATIONS_DIR.exists():
    PRESENTATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "presentations"

if PRESENTATIONS_DIR.exists():
    app.mount("/presentation", StaticFiles(directory=str(PRESENTATIONS_DIR), html=True), name="presentation")
    app.mount("/presentations", StaticFiles(directory=str(PRESENTATIONS_DIR), html=True), name="presentations")


@app.get("/", include_in_schema=False)
async def root_redirect():
    """
    Перенаправление с корня на панель оператора.
    """
    return RedirectResponse(url="/operator-panel")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """
    Предотвращение ошибок 404 при запросе favicon.ico браузером.
    """
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/health", status_code=status.HTTP_200_OK, tags=["System"])
async def health_check():
    """
    Эндпоинт для проверки работоспособности сервиса.
    Не требует авторизации по API Key.
    """
    return {"status": "healthy", "service": "intraservice-core-api"}


@app.get("/ready", tags=["System"])
async def readiness_check():
    checks: dict[str, str] = {}
    try:
        await verify_schema()
        checks["database"] = "ready"
    except Exception as exc:
        checks["database"] = f"failed:{type(exc).__name__}"
    try:
        await get_redis_client().ping()
        checks["redis"] = "ready"
    except Exception as exc:
        checks["redis"] = f"failed:{type(exc).__name__}"
    ready = all(value == "ready" for value in checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@app.get("/metrics", response_class=PlainTextResponse, tags=["System"])
async def command_metrics():
    async with AsyncSessionLocal() as db:
        status_rows = (await db.execute(
            select(CommandRecord.status, func.count(CommandRecord.id)).group_by(CommandRecord.status)
        )).all()
        pending_outbox = int(await db.scalar(
            select(func.count(CommandOutbox.id)).where(CommandOutbox.published_at.is_(None))
        ) or 0)
    lines = [
        "# HELP intralink_commands Commands by durable state",
        "# TYPE intralink_commands gauge",
    ]
    lines.extend(
        f'intralink_commands{{status="{command_status}"}} {count}'
        for command_status, count in status_rows
    )
    lines.extend([
        "# HELP intralink_command_outbox_pending Unpublished command messages",
        "# TYPE intralink_command_outbox_pending gauge",
        f"intralink_command_outbox_pending {pending_outbox}",
    ])
    return "\n".join(lines) + "\n"


@app.get("/health/rollout", status_code=status.HTTP_200_OK, tags=["System"])
async def rollout_health_check(expected_sha: str | None = None):
    """Fail-closed check for a new image before a proxy enables its traffic."""
    return await rollout_readiness(expected_sha=expected_sha)
