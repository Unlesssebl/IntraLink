"""IntraLink v2 Core Database Models (Fresh DB Clean State).

Only 5 active entities are maintained:
1. TaskKnowledgeBase (RAG dataset with pgvector HNSW index)
2. User (Engineers, admin users, credentials)
3. CommandRecord (Idempotent commands, outbox pattern)
4. TriageAudit (Immutable audit log of LLM triage decisions)
5. SystemState (System watermarks, poller cursors and settings)
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from core.database.base import GUID, Base, TimestampMixin

EMBEDDING_DIM = 1024


class TaskKnowledgeBase(Base, TimestampMixin):
    """RAG Knowledge Base holding historical solutions and vector embeddings."""

    __tablename__ = "task_knowledge_base"
    __table_args__ = (
        Index(
            "idx_task_kb_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
        Index("idx_task_kb_service_id", "service_id"),
        Index("idx_task_kb_quality", "quality_score"),
        Index(
            "ix_task_kb_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    task_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    original_name: Mapped[str] = mapped_column(String(500), nullable=False)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    solution: Mapped[str] = mapped_column(Text, nullable=False)

    service_id: Mapped[int] = mapped_column(Integer, nullable=False)
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    service_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status_name: Mapped[str] = mapped_column(String(100), nullable=False)

    classification_data: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )

    # 1024-dimensional BGE-M3 vector embedding
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(EMBEDDING_DIM),
        nullable=True,
    )

    # Full-Text Search TSVECTOR column (GIN indexed in PostgreSQL)
    search_vector: Mapped[Optional[Any]] = mapped_column(
        TSVECTOR().with_variant(Text(), "sqlite"),
        nullable=True,
    )

    is_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0", nullable=False)

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("is_blacklisted", False)
        kwargs.setdefault("quality_score", 1.0)
        kwargs.setdefault("classification_data", {})
        super().__init__(**kwargs)


class User(Base, TimestampMixin):
    """System and engineer user accounts."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    tg_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    auth_token_b64: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("is_admin", False)
        kwargs.setdefault("is_active", True)
        super().__init__(**kwargs)


class CommandRecord(Base, TimestampMixin):
    """Idempotent operational command state and execution journal."""

    __tablename__ = "commands"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    executor: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # 'api', 'worker', 'auto'
    target_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    params_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    initiator: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    result_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("status", "pending")
        kwargs.setdefault("priority", 5)
        kwargs.setdefault("target_json", {})
        kwargs.setdefault("params_json", {})
        super().__init__(**kwargs)


class TriageAudit(Base):
    """Immutable audit trail for LLM decisions, automated classification and redirects."""

    __tablename__ = "triage_audit"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model_used: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    context_snapshot: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    decision_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    applied_by: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("applied", False)
        kwargs.setdefault("applied_by", "system")
        kwargs.setdefault("prompt_tokens", 0)
        kwargs.setdefault("completion_tokens", 0)
        kwargs.setdefault("context_snapshot", {})
        kwargs.setdefault("decision_json", {})
        super().__init__(**kwargs)


class SystemState(Base, TimestampMixin):
    """System-wide state, cursors and ingestion watermarks."""

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    last_poll_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    state_data: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("state_data", {})
        super().__init__(**kwargs)


class AutopilotPolicy(Base, TimestampMixin):
    """Autopilot governance policy per scenario with Circuit Breaker tracking."""

    __tablename__ = "autopilot_policies"

    scenario_key: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="ASSISTED", server_default="ASSISTED")
    min_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.85, server_default="0.85")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_failure_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_circuit_broken: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("mode", "ASSISTED")
        kwargs.setdefault("min_confidence", 0.85)
        kwargs.setdefault("consecutive_failures", 0)
        kwargs.setdefault("is_circuit_broken", False)
        super().__init__(**kwargs)

