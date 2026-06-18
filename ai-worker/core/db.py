from collections.abc import AsyncGenerator
from sqlalchemy import BigInteger, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from core.config import settings

# Настройка асинхронного движка SQLAlchemy
engine = create_async_engine(settings.DATABASE_URL, echo=False)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)


# Базовый класс для моделей
class Base(DeclarativeBase):
    pass


# Модель пользователя (для совместимости/информации)
class User(Base):
    __tablename__ = "users"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    is_login: Mapped[str] = mapped_column(String, nullable=False)
    is_password_b64: Mapped[str] = mapped_column(String, nullable=False)
    is_user_id: Mapped[int] = mapped_column(Integer, nullable=True)
    last_task_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_comment_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_check_time: Mapped[str] = mapped_column(String, nullable=True)


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
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.EMBEDDING_DIMENSION), nullable=True)

    # Черный список (удаленные задачи)
    is_blacklisted: Mapped[bool] = mapped_column(default=False, server_default="false")


# Зависимость для получения сессии базы данных
async def get_db() -> AsyncGenerator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# Функция инициализации БД (создание таблиц и расширения vector)
async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        await conn.run_sync(Base.metadata.create_all)
        # Гарантируем наличие колонки is_blacklisted в случае обновления схемы существующей БД
        await conn.execute(text(
            "ALTER TABLE task_knowledge_base ADD COLUMN IF NOT EXISTS is_blacklisted BOOLEAN NOT NULL DEFAULT false;"
        ))
        # Гарантируем, что колонка embedding может принимать NULL значения
        await conn.execute(text(
            "ALTER TABLE task_knowledge_base ALTER COLUMN embedding DROP NOT NULL;"
        ))
