"""IntraLink v2 Core Database Models (Fresh DB Clean State).

Only 5 active entities are maintained:
1. TaskKnowledgeBase (RAG dataset with pgvector HNSW index)
2. User (Engineers, admin users, credentials)
3. CommandRecord (Idempotent commands, outbox pattern)
4. TriageAudit (Immutable audit log of LLM triage decisions)
5. SystemState (System watermarks, poller cursors and settings)
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
    func,
    text,
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
    decision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("routing_decisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("prepared_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    preflight_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("routing_preflights.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
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
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_failure_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_circuit_broken: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("mode", "ASSISTED")
        kwargs.setdefault("consecutive_failures", 0)
        kwargs.setdefault("is_circuit_broken", False)
        super().__init__(**kwargs)


_SECRET_STRING_PATTERNS = [
    re.compile(r"(?i)(password|пароль|secret|token|auth_b64|field1489|1489)[\s:=]+([^\s,;]+)"),
]


def sanitize_secret_text(text: str) -> str:
    """Mask password and credential patterns inside arbitrary strings."""
    if not text:
        return text
    sanitized = text
    for pattern in _SECRET_STRING_PATTERNS:
        sanitized = pattern.sub(r"\1: ***REDACTED***", sanitized)
    return sanitized


def sanitize_secrets(data: Any) -> Any:
    """Recursively mask sensitive values (passwords, tokens, credentials) in dicts, lists, and strings."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(secret_kw in k_lower for secret_kw in ("password", "token", "secret", "auth_b64", "pass", "field1489", "1489")):
                sanitized[k] = "***REDACTED***"
            else:
                sanitized[k] = sanitize_secrets(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_secrets(item) for item in data]
    elif isinstance(data, str):
        return sanitize_secret_text(data)
    return data


class RoutingDecisionRecord(Base, TimestampMixin):
    """Immutable audit log and persistent record of an Evidence-Based Routing Cascade decision."""

    __tablename__ = "routing_decisions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    router_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    selected_scenario: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    selected_scenario_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    snapshot_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    candidates_json: Mapped[List[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    evidence_json: Mapped[List[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    verifier_result_json: Mapped[Optional[List[Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    missing_facts_json: Mapped[List[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    degradation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decision_reason_codes_json: Mapped[List[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    degraded_components_json: Mapped[Dict[str, str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    verifier_trace_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("snapshot_json", {})
        kwargs.setdefault("candidates_json", [])
        kwargs.setdefault("evidence_json", [])
        kwargs.setdefault("missing_facts_json", [])
        kwargs.setdefault("decision_reason_codes_json", [])
        kwargs.setdefault("degraded_components_json", {})
        super().__init__(**kwargs)


class RoutingFeedbackRecord(Base, TimestampMixin):
    """Operator supervisor feedback and corrections on routing cascade decisions."""

    __tablename__ = "routing_feedback"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("routing_decisions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    prepared_plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("prepared_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    command_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("commands.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    snapshot_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    operator_username: Mapped[str] = mapped_column(String(100), nullable=False, default="operator")
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # approved, corrected, rejected, manual_takeover
    original_scenario: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    corrected_scenario: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    original_params: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    corrected_params: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    reason_tag: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    operator_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    router_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    verifier_used: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="runtime", index=True)

    __table_args__ = (
        Index(
            "uq_routing_feedback_terminal_plan",
            "prepared_plan_id",
            unique=True,
            postgresql_where=text(
                "verdict IN ('approved', 'corrected', 'rejected', 'manual_takeover') "
                "AND prepared_plan_id IS NOT NULL"
            ),
            sqlite_where=text(
                "verdict IN ('approved', 'corrected', 'rejected', 'manual_takeover') "
                "AND prepared_plan_id IS NOT NULL"
            ),
        ),
    )

    def __init__(self, **kwargs: Any) -> None:
        if "original_params" in kwargs and kwargs["original_params"]:
            kwargs["original_params"] = sanitize_secrets(kwargs["original_params"])
        if "corrected_params" in kwargs and kwargs["corrected_params"]:
            kwargs["corrected_params"] = sanitize_secrets(kwargs["corrected_params"])
        if "operator_notes" in kwargs and kwargs["operator_notes"] and not kwargs.get("notes"):
            kwargs["notes"] = sanitize_secrets(kwargs["operator_notes"])
            kwargs["operator_notes"] = kwargs["notes"]
        elif "notes" in kwargs and kwargs["notes"] and not kwargs.get("operator_notes"):
            kwargs["operator_notes"] = sanitize_secrets(kwargs["notes"])
            kwargs["notes"] = kwargs["operator_notes"]
        kwargs.setdefault("operator_username", "operator")
        kwargs.setdefault("original_params", {})
        kwargs.setdefault("corrected_params", {})
        kwargs.setdefault("source", "runtime")
        kwargs.setdefault("verifier_used", False)
        super().__init__(**kwargs)


@event.listens_for(RoutingFeedbackRecord, "before_insert")
@event.listens_for(RoutingFeedbackRecord, "before_update")
def _sanitize_routing_feedback_listener(mapper: Any, connection: Any, target: RoutingFeedbackRecord) -> None:
    if target.original_params:
        target.original_params = sanitize_secrets(target.original_params)
    if target.corrected_params:
        target.corrected_params = sanitize_secrets(target.corrected_params)
    if target.operator_notes:
        target.operator_notes = sanitize_secrets(target.operator_notes)
    if target.notes:
        target.notes = sanitize_secrets(target.notes)


@event.listens_for(CommandRecord, "before_insert")
@event.listens_for(CommandRecord, "before_update")
def _sanitize_command_record_listener(mapper: Any, connection: Any, target: CommandRecord) -> None:
    if target.params_json:
        target.params_json = sanitize_secrets(target.params_json)


class RoutingPreflightRecord(Base, TimestampMixin):
    """Append-only audit record and short-lived state of read-only preflight verification."""

    __tablename__ = "routing_preflights"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("routing_decisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scenario_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # passed, failed, degraded, not_applicable
    checks_json: Mapped[List[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    details_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    def __init__(self, **kwargs: Any) -> None:
        if "details_json" in kwargs and kwargs["details_json"]:
            kwargs["details_json"] = sanitize_secrets(kwargs["details_json"])
        kwargs.setdefault("checks_json", [])
        kwargs.setdefault("details_json", {})
        super().__init__(**kwargs)


class PreparedPlanRecord(Base, TimestampMixin):
    """Persistent prepared operator action plan derived from evidence decision and preflight."""

    __tablename__ = "prepared_plans"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("routing_decisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scenario_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    preflight_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("routing_preflights.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    def __init__(self, **kwargs: Any) -> None:
        if "plan_json" in kwargs and kwargs["plan_json"]:
            kwargs["plan_json"] = sanitize_secrets(kwargs["plan_json"])
        kwargs.setdefault("plan_json", {})
        kwargs.setdefault("state", "ready")
        super().__init__(**kwargs)
