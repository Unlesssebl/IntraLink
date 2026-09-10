import datetime
import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, FetchedValue, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.config import settings

# Определение типов, совместимых и с PostgreSQL, и с SQLite (для unit-тестов)
JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
UUID_TYPE = Uuid(as_uuid=True).with_variant(UUID(as_uuid=True), "postgresql")
TSVECTOR_TYPE = Text().with_variant(TSVECTOR(), "postgresql")
ARRAY_INT_TYPE = JSON().with_variant(ARRAY(Integer), "postgresql")

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

CURRENT_SCHEMA_REVISION = "20260909_0012"




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


class Principal(Base):
    """A stable human or service identity used for authorization and audit."""

    __tablename__ = "principals"
    __table_args__ = (UniqueConstraint("type", "subject", name="uq_principal_type_subject"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", server_default="active", index=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Role(Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(String(300), nullable=False)


class Permission(Base):
    __tablename__ = "permissions"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    description: Mapped[str] = mapped_column(String(300), nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_name: Mapped[str] = mapped_column(
        String(64), ForeignKey("roles.name", ondelete="CASCADE"), primary_key=True
    )
    permission_name: Mapped[str] = mapped_column(
        String(100), ForeignKey("permissions.name", ondelete="CASCADE"), primary_key=True
    )


class PrincipalRole(Base):
    __tablename__ = "principal_roles"

    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("principals.id", ondelete="CASCADE"), primary_key=True
    )
    role_name: Mapped[str] = mapped_column(
        String(64), ForeignKey("roles.name", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("principals.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ServiceCredential(Base):
    __tablename__ = "service_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("principals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    secret_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes_json: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("principals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    rotated_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("auth_sessions.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TelegramLink(Base):
    __tablename__ = "telegram_links"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("principals.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending_reverification", server_default="pending_reverification"
    )
    verified_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TelegramLinkCode(Base):
    __tablename__ = "telegram_link_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("principals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApprovalChallenge(Base):
    __tablename__ = "approval_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    command_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("commands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("principals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    allowed_decisions_json: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SecurityEvent(Base):
    """Append-only identity and authorization audit without credentials or tokens."""

    __tablename__ = "security_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    principal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("principals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    auth_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


# Модель базы знаний RAG (датасета заявок)
class TaskKnowledgeBase(Base):
    __tablename__ = "task_knowledge_base"
    __table_args__ = (
        Index(
            "idx_task_kb_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )

    task_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    original_name: Mapped[str] = mapped_column(String, nullable=False)
    problem: Mapped[str] = mapped_column(String, nullable=False)
    solution: Mapped[str] = mapped_column(String, nullable=False)

    service_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    service_name: Mapped[str] = mapped_column(String, nullable=False)
    service_path: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    service_path_ids: Mapped[list[int] | None] = mapped_column(ARRAY_INT_TYPE, nullable=True)
    status_name: Mapped[str] = mapped_column(String, nullable=False)

    classification_data: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)

    # Колонка вектора эмбеддингов (nullable для заблокированных записей)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.EMBEDDING_DIMENSION), nullable=True
    )

    # Черный список (удаленные задачи)
    is_blacklisted: Mapped[bool] = mapped_column(default=False, server_default="false")

    # Скоринг ценности решения (0.0 - 1.0), по умолчанию 1.0
    quality_score: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1.0", nullable=False, index=True
    )

    # Полнотекстовый поисковый вектор (tsvector в PostgreSQL, Text в SQLite)
    # Вычисляется СУБД (GENERATED ALWAYS AS ... STORED в PostgreSQL)
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR_TYPE,
        server_default=FetchedValue(),
        server_onupdate=FetchedValue(),
        nullable=True,
    )



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


class AutopilotSetting(Base):
    """Database-backed global gate for all automatic ticket cycles."""

    __tablename__ = "autopilot_settings"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AutopilotSettingEvent(Base):
    """Append-only audit trail for changes to the global autopilot gate."""

    __tablename__ = "autopilot_setting_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    setting_key: Mapped[str] = mapped_column(
        String(32), ForeignKey("autopilot_settings.key", ondelete="CASCADE"), nullable=False, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class AutopilotScenario(Base):
    """Explicit allowlist of IntraService services supported by autopilot."""

    __tablename__ = "autopilot_scenarios"
    __table_args__ = (
        UniqueConstraint("service_id", "scenario_key", name="uq_autopilot_scenario_service"),
        CheckConstraint(
            "rollout_mode IN ('legacy', 'shadow', 'canary', 'active')",
            name="ck_autopilot_scenario_rollout_mode",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    service_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    scenario_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    rollout_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    config_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TicketRun(Base):
    """Durable manual or autopilot cycle for one IntraService ticket."""

    __tablename__ = "ticket_runs"
    __table_args__ = (
        UniqueConstraint("task_id", "trigger_key", name="uq_ticket_run_trigger"),
        Index(
            "uq_ticket_run_active_task",
            "task_id",
            unique=True,
            postgresql_where=text("completed_at IS NULL"),
            sqlite_where=text("completed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    trigger_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_key: Mapped[str] = mapped_column(String(160), nullable=False)
    trigger_snapshot_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    scenario_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scenario_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fact_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    context_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    decision_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    waiting_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    waiting_until: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clarification_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    pause_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TicketRunEvent(Base):
    """Append-only state and decision history for a ticket cycle."""

    __tablename__ = "ticket_run_events"
    __table_args__ = (
        UniqueConstraint("ticket_run_id", "sequence", name="uq_ticket_run_event_sequence"),
        UniqueConstraint("ticket_run_id", "event_key", name="uq_ticket_run_event_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    ticket_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class TicketFactObservation(Base):
    """Append-only provenance for facts collected during a ticket run."""

    __tablename__ = "ticket_fact_observations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('missing','valid','invalid','ambiguous','conflicting','stale')",
            name="ck_ticket_fact_observation_state",
        ),
        CheckConstraint(
            "source_kind IN ('structured_field','directory','diagnostic','comment','parser','llm','operator')",
            name="ck_ticket_fact_observation_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    ticket_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("ticket_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fact_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    value_json: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSON_TYPE, nullable=True
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    source_kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_span: Mapped[str | None] = mapped_column(Text, nullable=True)
    sensitivity: Mapped[str] = mapped_column(
        String(16), nullable=False, default="internal", server_default="internal"
    )
    metadata_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    observed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("ticket_fact_observations.id", ondelete="SET NULL"),
        nullable=True,
    )
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class DecisionRecord(Base):
    """Durable, versioned explanation for a triage or autopilot decision."""

    __tablename__ = "decision_records"
    __table_args__ = (
        UniqueConstraint("task_id", "version", name="uq_decision_task_version"),
        UniqueConstraint(
            "task_id", "context_fingerprint", "analysis_kind", name="uq_decision_context"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    ticket_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("ticket_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    previous_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("decision_records.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    context_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    context_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    envelope_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    build_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    finalized_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def source_json(self) -> dict:
        return (self.envelope_json or {}).get("source") or {}

    @source_json.setter
    def source_json(self, value: dict) -> None:
        if not isinstance(self.envelope_json, dict):
            self.envelope_json = {}
        self.envelope_json["source"] = value or {}

    @property
    def completeness_json(self) -> dict:
        return (self.envelope_json or {}).get("completeness") or {}

    @completeness_json.setter
    def completeness_json(self, value: dict) -> None:
        if not isinstance(self.envelope_json, dict):
            self.envelope_json = {}
        self.envelope_json["completeness"] = value or {}

    @property
    def proposal_json(self) -> dict:
        env = self.envelope_json or {}
        outcome = env.get("outcome") or {}
        response = env.get("response") or {}
        gates = env.get("gates") or {}
        return {
            "decision_envelope": env,
            "comment": response.get("text"),
            "ready": bool(gates.get("can_send_response") or gates.get("can_execute_action")),
            "status_id": outcome.get("target_status_id"),
            "action": outcome.get("action"),
            **outcome,
        }

    @proposal_json.setter
    def proposal_json(self, value: dict) -> None:
        if not isinstance(self.envelope_json, dict):
            self.envelope_json = {}
        val = value or {}
        if "decision_envelope" in val and isinstance(val["decision_envelope"], dict):
            self.envelope_json = {**self.envelope_json, **val["decision_envelope"]}
        else:
            outcome = self.envelope_json.setdefault("outcome", {})
            if "status_id" in val:
                outcome["target_status_id"] = val["status_id"]
            if "action" in val:
                outcome["action"] = val["action"]
            if "ready" in val:
                gates = self.envelope_json.setdefault("gates", {})
                gates["can_send_response"] = val["ready"]
                gates["can_execute_action"] = val["ready"]
            if "comment" in val:
                resp = self.envelope_json.setdefault("response", {})
                resp["text"] = val["comment"]
                resp.setdefault("state", "valid")

    @property
    def policy_json(self) -> dict:
        return (self.envelope_json or {}).get("policy") or {}

    @policy_json.setter
    def policy_json(self, value: dict) -> None:
        if not isinstance(self.envelope_json, dict):
            self.envelope_json = {}
        self.envelope_json["policy"] = value or {}


class DecisionStep(Base):
    """Append-only facts produced by one component while making a decision."""

    __tablename__ = "decision_steps"
    __table_args__ = (
        UniqueConstraint("decision_id", "sequence", name="uq_decision_step_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("decision_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    component: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    input_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    output_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DecisionFeedback(Base):
    """Operator review tied to the exact version of a decision."""

    __tablename__ = "decision_feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("decision_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_action_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class DecisionApplication(Base):
    """Exactly-once projection of a verified Command v2 application."""

    __tablename__ = "decision_applications"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("decision_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    command_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("commands.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    proposed_action_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    applied_action_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    verified_result_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    operator: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class CommandRecord(Base):
    """Authoritative v2 command state. Redis only transports its outbox events."""

    __tablename__ = "commands"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    executor: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    target_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    params_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    initiator: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    initiator_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("principals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    ticket_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("ticket_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("decision_records.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    result_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preflight_evidence_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    plan_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        server_default=func.now(),
        index=True,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommandEvent(Base):
    __tablename__ = "command_events"
    __table_args__ = (UniqueConstraint("command_id", "sequence", name="uq_command_event_sequence"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("commands.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    details_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class CommandSecretArtifact(Base):
    """Encrypted, short-lived, one-time material produced by a command."""

    __tablename__ = "command_secret_artifacts"
    __table_args__ = (UniqueConstraint("command_id", "name", name="uq_command_secret_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    command_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("commands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False, default="text/plain")
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CommandOutbox(Base):
    __tablename__ = "command_outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("commands.id", ondelete="CASCADE"), nullable=False, index=True)
    stream: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CommandInbox(Base):
    """Durable record of a transport message accepted by a worker."""

    __tablename__ = "command_inbox"
    __table_args__ = (
        UniqueConstraint("consumer", "message_id", name="uq_command_inbox_message"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    command_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("commands.id", ondelete="CASCADE"), nullable=False, index=True
    )
    consumer: Mapped[str] = mapped_column(String(100), nullable=False)
    message_id: Mapped[str] = mapped_column(String(100), nullable=False)
    outbox_id: Mapped[uuid.UUID | None] = mapped_column(UUID_TYPE, nullable=True, index=True)
    received_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class CommandAttempt(Base):
    __tablename__ = "command_attempts"
    __table_args__ = (UniqueConstraint("command_id", "attempt_no", name="uq_command_attempt_no"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("commands.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_json: Mapped[dict | None] = mapped_column(JSON_TYPE, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommandApproval(Base):
    __tablename__ = "command_approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("commands.id", ondelete="CASCADE"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    approver_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("principals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    command_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    plan_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActionPolicyRecord(Base):
    __tablename__ = "action_policies"

    action: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SecurityAuditLog(Base):
    """Append-only audit events for safety gates; never stores source prompts or PII."""

    __tablename__ = "security_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    details_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
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


class ResponseTemplate(Base):
    """Versioned response text. Keys are immutable; versions are append-only."""

    __tablename__ = "response_templates"
    __table_args__ = (
        UniqueConstraint("key", "version", name="uq_response_template_key_version"),
        Index(
            "uq_response_template_active_key",
            "key",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    required_variables: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by: Mapped[str] = mapped_column(String(100), nullable=False, default="system:migration", server_default="system:migration")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ResolutionPolicy(Base):
    """Versioned mapping from a domain outcome to presentation/action policy."""

    __tablename__ = "resolution_policies"
    __table_args__ = (
        UniqueConstraint("outcome_key", "version", name="uq_resolution_policy_key_version"),
        CheckConstraint("outcome_kind IN ('clarification','action','manual_review','resolution')", name="ck_resolution_policy_kind"),
        CheckConstraint("risk_level BETWEEN 0 AND 3", name="ck_resolution_policy_risk"),
        CheckConstraint("target_status_id IS NULL OR target_status_id IN (27,29,30,35,48)", name="ck_resolution_policy_status"),
        Index(
            "uq_resolution_policy_active_key",
            "outcome_key",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    outcome_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    outcome_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    template_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("response_templates.id", ondelete="RESTRICT"), nullable=True)
    target_status_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expenses: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default="10")
    action_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by: Mapped[str] = mapped_column(String(100), nullable=False, default="system:migration", server_default="system:migration")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


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


# Зависимость (dependency) для получения сессии базы данных в FastAPI
async def get_db() -> AsyncGenerator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# SQLite-only schema bootstrap for isolated tests. PostgreSQL uses Alembic.
async def init_db() -> None:
    if not settings.DATABASE_URL.startswith("sqlite"):
        raise RuntimeError("init_db is disabled for PostgreSQL; run 'alembic upgrade head'")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def verify_schema() -> None:
    """Fail closed when production migrations have not reached this build."""
    async with engine.connect() as conn:
        revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != CURRENT_SCHEMA_REVISION:
            raise RuntimeError(
                f"Database revision {revision!r} does not match {CURRENT_SCHEMA_REVISION!r}"
            )
        commands_table = await conn.scalar(text("SELECT to_regclass('public.commands')"))
        if not commands_table:
            raise RuntimeError("Required table 'commands' is missing")

