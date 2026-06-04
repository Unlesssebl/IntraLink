import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, status

from app.database.db import init_db
from app.routers import auth, tasks, users

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Действия при запуске приложения
    logger.info("Инициализация базы данных...")
    try:
        await init_db()
        logger.info("База данных успешно инициализирована.")
    except Exception as e:
        logger.exception("Ошибка при инициализации базы данных: %s", e)
    
    # Запуск фонового воркера опроса и публикации событий
    from app.services.worker import start_worker, stop_worker
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

app = FastAPI(
    title="IntraService Core API Gateway",
    description="Микросервис-шлюз для интеграции с API IntraService",
    version="1.0.0",
    lifespan=lifespan
)

# Подключение роутеров с единым префиксом версии API v1
app.include_router(auth.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")

@app.get("/health", status_code=status.HTTP_200_OK, tags=["System"])
async def health_check():
    """
    Эндпоинт для проверки работоспособности сервиса.
    Не требует авторизации по API Key.
    """
    return {"status": "healthy", "service": "intraservice-core-api"}
