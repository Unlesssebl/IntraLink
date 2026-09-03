import datetime
import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, JSON, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.config import settings

# Определение типов, совместимых и с PostgreSQL, и с SQLite (для unit-тестов)
JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
UUID_TYPE = Uuid(as_uuid=True).with_variant(UUID(as_uuid=True), "postgresql")

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

    classification_data: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)

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
        UUID_TYPE, primary_key=True, default=uuid.uuid4
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    command_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    params_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    mode: Mapped[str] = mapped_column(String(20), default="auto", server_default="auto")
    initiator: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued", index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=5, server_default="5")

    # Результат
    result_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
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


class DesktopLaunchLog(Base):
    """Аудит безопасных локальных запусков из IntraLink Desktop Companion."""

    __tablename__ = "desktop_launch_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    completion_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    client: Mapped[str] = mapped_column(String(32), nullable=False)
    initiator: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="issued", index=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    claimed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# Модель шаблонов ответов триажа (SSOT)
class TriageTemplate(Base):
    __tablename__ = "triage_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="in_work", server_default="in_work"
    )
    status_id: Mapped[int] = mapped_column(
        Integer, nullable=False, default=27, server_default="27"
    )
    status_name: Mapped[str] = mapped_column(
        String(100), nullable=False, default="В работе", server_default="'В работе'"
    )
    expenses: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Модель детерминированных правил триажа (Rule Engine)
class TriageRule(Base):
    __tablename__ = "triage_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100", index=True
    )
    conditions_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    target_template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    actions_override_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Журнал аудита изменений правил и шаблонов
class RuleAuditLog(Base):
    __tablename__ = "rule_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid.uuid4
    )
    entity_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )  # 'template' | 'rule'
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    changed_by: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    change_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # 'create' | 'update' | 'delete' | 'seed'
    diff_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


# Модель системных настроек и интеграций (LDAPS, профили инженера, IntraService)
class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    value_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    is_encrypted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Журнал аудита и обратной связи по решениям триажа (Feedback Loop)
class TriageAuditLog(Base):
    __tablename__ = "triage_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    generated_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_comment: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_score: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1.0"
    )
    diff_ratio: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0.0"
    )
    operator_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    status_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
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
        if not settings.DATABASE_URL.startswith("sqlite"):
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"))
        await conn.run_sync(Base.metadata.create_all)
        if not settings.DATABASE_URL.startswith("sqlite"):
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

    # Создание индекса HNSW в отдельной транзакции (pgvector HNSW строго ограничен 2000 измерениями)
    if not settings.DATABASE_URL.startswith("sqlite") and settings.EMBEDDING_DIMENSION <= 2000:
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_task_kb_hnsw
                        ON task_knowledge_base
                        USING hnsw (embedding vector_cosine_ops)
                        WITH (m = 16, ef_construction = 64);
                        """
                    )
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Не удалось создать HNSW индекс для pgvector: %s", e)

