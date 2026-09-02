import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database.db import AsyncSessionLocal, init_db
from app.routers import (
    admin,
    ai,
    ai_worker,
    auth,
    commands,
    events,
    rules_admin,
    service_tasks,
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
        await init_db()
        logger.info("База данных успешно инициализирована.")
    except Exception as e:
        logger.exception("Ошибка при инициализации базы данных: %s", e)

    # Database Seeding и прогрев L1 кэша шаблонов
    try:
        async with AsyncSessionLocal() as session:
            await seed_templates_if_empty(session)
            await get_templates_from_db(session)
        logger.info("L1 кэш шаблонов триажа инициализирован из PostgreSQL.")
    except Exception as e:
        logger.warning("Ошибка инициализации шаблонов триажа: %s", e)

    # Запуск Pub/Sub слушателя инвалидации кэша правил
    invalidation_task = asyncio.create_task(
        start_rules_invalidation_listener(settings.REDIS_URL)
    )

    logger.info("Инициализация HTTP-сессии IntraService...")
    try:
        await init_session()
    except Exception as e:
        logger.exception("Ошибка при инициализации HTTP-сессии: %s", e)

    try:
        await start_worker()
    except Exception as e:
        logger.exception("Ошибка при запуске фонового воркера: %s", e)

    yield
    # Действия при остановке приложения
    logger.info("Остановка приложения Core API...")
    invalidation_task.cancel()
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
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

# Разрешение CORS для локальных веб-клиентов и интерфейсов
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://.*$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
app.include_router(events.router)
app.include_router(admin.router)
app.include_router(ai_worker.router)

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
    Перенаправление с корня на панель администратора.
    """
    return RedirectResponse(url="/admin")


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
