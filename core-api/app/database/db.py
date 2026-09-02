import datetime
import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.config import settings

# Настройка асинхронного движка SQLAlchemy с устойчивым пулом соединений
engine_kwargs: dict = {"echo": False}
if not settings.DATABASE_URL.startswith("sqlite"):
    engine_kwargs.update(
        {
            "pool_size": 20,
            "max_overflow": 20,
            "pool_pre_ping": True,
            "pool_recycle": 300,
        }
    )

engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)



# Базовый класс для моделей
class Base(DeclarativeBase):
    pass


# Модель пользователя
class User(Base):
    __tablename__ = "users"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    is_login: Mapped[str] = mapped_column(String, nullable=False)
    is_password_b64: Mapped[str] = mapped_column(String, nullable=False)
    is_user_id: Mapped[int] = mapped_column(Integer, nullable=True)
    last_task_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_comment_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_check_time: Mapped[str] = mapped_column(
        String, nullable=True
    )  # Храним в виде строки ISO, как было в боте


# Модель базы знаний RAG (датасета заявок)
class TaskKnowledgeBase(Base):
    __tablename__ = "task_knowledge_base"

    task_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    original_name: Mapped[str] = mapped_column(String, nullable=False)
    problem: Mapped[str] = mapped_column(String, nullable=False)
    solution: Mapped[str] = mapped_column(String, nullable=False)

    service_id: Mapped[int] = mapped_column(Integer, nullable=False)
    service_name: Mapped[str] = mapped_column(String, nullable=False)
    status_name: Mapped[str] = mapped_column(String, nullable=False)

    classification_data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Колонка вектора эмбеддингов (nullable для заблокированных записей)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.EMBEDDING_DIMENSION), nullable=True
    )

    # Черный список (удаленные задачи)
    is_blacklisted: Mapped[bool] = mapped_column(default=False, server_default="false")


# Модель журнала исполнения задач (Command Bus / Execution Hub)
class JobLog(Base):
    __tablename__ = "job_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    command_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    params_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    mode: Mapped[str] = mapped_column(String(20), default="auto", server_default="auto")
    initiator: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued", index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=5, server_default="5")

    # Результат
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Связь с IntraService
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # Временные метки
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# Зависимость (dependency) для получения сессии базы данных в FastAPI
async def get_db() -> AsyncGenerator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# Функция инициализации БД (создание таблиц)
async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"))
        await conn.run_sync(Base.metadata.create_all)
        # Гарантируем наличие колонки is_blacklisted в случае обновления схемы существующей БД
        await conn.execute(
            text(
                "ALTER TABLE task_knowledge_base ADD COLUMN IF NOT EXISTS is_blacklisted BOOLEAN NOT NULL DEFAULT false;"
            )
        )
        # Гарантируем, что колонка embedding может принимать NULL значения
        await conn.execute(
            text(
                "ALTER TABLE task_knowledge_base ALTER COLUMN embedding DROP NOT NULL;"
            )
        )

