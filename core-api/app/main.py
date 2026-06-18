import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.responses import RedirectResponse

from app.database.db import init_db
from app.routers import admin, auth, service_tasks, tasks, users, ai_worker
from app.services.intraservice import close_session, init_session
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
    try:
        await stop_worker()
    except Exception as e:
        logger.exception("Ошибка при остановке фонового воркера: %s", e)

    logger.info("Закрытие HTTP-сессии IntraService...")
    try:
        await close_session()
    except Exception as e:
        logger.exception("Ошибка при закрытии HTTP-сессии: %s", e)


app = FastAPI(
    title="IntraService Core API Gateway",
    description="Микросервис-шлюз для интеграции с API IntraService",
    version="1.0.0",
    lifespan=lifespan,
)

# Подключение роутеров с единым префиксом версии API v1
app.include_router(auth.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(service_tasks.router, prefix="/api/v1")
app.include_router(admin.router)
app.include_router(ai_worker.router)


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
