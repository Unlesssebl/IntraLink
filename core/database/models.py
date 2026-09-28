"""IntraLink v2 Core Database Models (Fresh DB Clean State).

Active entities include:
1. TaskKnowledgeBase (RAG dataset with pgvector HNSW index)
2. User (Engineers, admin users, credentials)
3. CommandRecord (Idempotent commands, outbox pattern)
4. SystemState (System watermarks, poller cursors and settings)
5. ADR 0006 case, workflow, action and feedback records
"""

import re
import uuid
from datetime import datetime
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
    action_plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        ForeignKey("action_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    capability_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    sequence_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    params_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    plan_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    snapshot_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
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


class CaseDecisionRecord(Base, TimestampMixin):
    """Immutable intake frame and case-type decision for one ticket snapshot."""

    __tablename__ = "case_decisions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    frame_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    frame_version: Mapped[str] = mapped_column(String(32), nullable=False)
    router_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    primary_case_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    case_frame_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    decision_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )

    def __init__(self, **kwargs: Any) -> None:
        kwargs["case_frame_json"] = sanitize_secrets(kwargs.get("case_frame_json", {}))
        kwargs["decision_json"] = sanitize_secrets(kwargs.get("decision_json", {}))
        super().__init__(**kwargs)


class ServiceCatalogVersionRecord(Base, TimestampMixin):
    """Atomic, immutable snapshot of the read-only IntraService service catalog."""

    __tablename__ = "service_catalog_versions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    catalog_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)


class ServiceCatalogEntryRecord(Base, TimestampMixin):
    __tablename__ = "service_catalog_entries"
    __table_args__ = (
        Index("uq_service_catalog_entry_version_service", "catalog_version_id", "service_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    catalog_version_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("service_catalog_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    service_path: Mapped[str] = mapped_column(Text, nullable=False)
    parent_service_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    task_type_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    form_metadata_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    field_metadata_json: Mapped[List[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    catalog_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class ServiceRouteBindingRecord(Base, TimestampMixin):
    __tablename__ = "service_route_bindings"
    __table_args__ = (
        Index("uq_service_route_binding_key_version_catalog", "key", "version", "catalog_hash", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    service_ids_json: Mapped[List[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    allowed_case_types_json: Mapped[List[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    default_case_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    allowed_workflows_json: Mapped[List[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    allowed_capabilities_json: Mapped[List[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    required_task_type_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    required_fields_json: Mapped[List[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    redirect_strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    is_validated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    catalog_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class ServiceCompatibilityDecisionRecord(Base, TimestampMixin):
    __tablename__ = "service_compatibility_decisions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_decision_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("case_decisions.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    source_service_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    binding_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    binding_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    catalog_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    decision_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )


class ServiceRoutingFeedbackRecord(Base, TimestampMixin):
    __tablename__ = "service_routing_feedback"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    compatibility_decision_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("service_compatibility_decisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    operator_username: Mapped[str] = mapped_column(String(100), nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    selected_target_service_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    reason_tag: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class RedirectPlanRecord(Base, TimestampMixin):
    __tablename__ = "redirect_plans"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_decision_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("case_decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    compatibility_decision_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("service_compatibility_decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_service_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_service_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    target_service_path: Mapped[str] = mapped_column(Text, nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    rendered_public_comment: Mapped[str] = mapped_column(Text, nullable=False)
    catalog_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    binding_key: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_version: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    approval_state: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_state: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    plan_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    approved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class RedirectFeedbackRecord(Base, TimestampMixin):
    __tablename__ = "redirect_feedback"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    redirect_plan_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("redirect_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    operator_username: Mapped[str] = mapped_column(String(100), nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    selected_target_service_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason_tag: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class WorkflowPlanRecord(Base, TimestampMixin):
    """Versioned business-process state derived from a CaseDecision."""

    __tablename__ = "workflow_plans"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    case_decision_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("case_decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workflow_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workflow_version: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    clarification_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    plan_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )

    def __init__(self, **kwargs: Any) -> None:
        kwargs["plan_json"] = sanitize_secrets(kwargs.get("plan_json", {}))
        kwargs.setdefault("clarification_round", 0)
        super().__init__(**kwargs)


class ClarificationRequestRecord(Base, TimestampMixin):
    """Durable, idempotent request for missing public onboarding facts."""

    __tablename__ = "clarification_requests"
    __table_args__ = (Index("ix_clarification_requests_task_state", "task_id", "state"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    workflow_plan_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("workflow_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_key: Mapped[str] = mapped_column(String(64), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    missing_facts_json: Mapped[List[Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    baseline_event_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    response_event_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    response_facts_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("missing_facts_json", [])
        kwargs["response_facts_json"] = sanitize_secrets(kwargs.get("response_facts_json", {}))
        super().__init__(**kwargs)


class OnboardingFactCorrectionRecord(Base, TimestampMixin):
    """Operator-confirmed canonical onboarding fact, append-only by snapshot."""

    __tablename__ = "onboarding_fact_corrections"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    fact_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    operator_username: Mapped[str] = mapped_column(String(100), nullable=False)
    reason_tag: Mapped[str] = mapped_column(String(64), nullable=False)


class ActionPlanRecord(Base, TimestampMixin):
    """Canonical, approval-bound list of capability invocations."""

    __tablename__ = "action_plans"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    workflow_plan_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("workflow_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_decision_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("case_decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workflow_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workflow_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_service_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    service_binding_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    service_binding_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    catalog_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    plan_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    approved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __init__(self, **kwargs: Any) -> None:
        kwargs["plan_json"] = sanitize_secrets(kwargs.get("plan_json", {}))
        super().__init__(**kwargs)


class ActionPreflightRecord(Base, TimestampMixin):
    """Append-only technical preflight result for one action."""

    __tablename__ = "action_preflights"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    action_plan_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    capability_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    checks_json: Mapped[List[Any]] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list)
    details_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("checks_json", [])
        kwargs["details_json"] = sanitize_secrets(kwargs.get("details_json", {}))
        if kwargs.get("error_message"):
            kwargs["error_message"] = sanitize_secret_text(kwargs["error_message"])
        super().__init__(**kwargs)


class CaseFeedbackRecord(Base, TimestampMixin):
    """Operator feedback about extraction and case-type selection only."""

    __tablename__ = "case_feedback"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    case_decision_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("case_decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    operator_username: Mapped[str] = mapped_column(String(100), nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    original_case_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    corrected_case_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    corrected_frame_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=True
    )
    reason_tag: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class PlanFeedbackRecord(Base, TimestampMixin):
    """Terminal operator feedback for an ActionPlan."""

    __tablename__ = "plan_feedback"
    __table_args__ = (Index("uq_plan_feedback_terminal", "action_plan_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    action_plan_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    operator_username: Mapped[str] = mapped_column(String(100), nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    original_plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    corrected_action_plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID, ForeignKey("action_plans.id", ondelete="SET NULL"), nullable=True
    )
    reason_tag: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ExecutionFeedbackRecord(Base, TimestampMixin):
    """Observed outcome for one capability command."""

    __tablename__ = "execution_feedback"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    action_plan_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    command_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("commands.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    capability_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    result_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )


class WorkflowPolicyRecord(Base, TimestampMixin):
    """Execution mode belongs to business workflows, never to case routing."""

    __tablename__ = "workflow_policies"

    workflow_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="ASSISTED", server_default="ASSISTED")
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class CapabilityHealthRecord(Base, TimestampMixin):
    """Runtime health and circuit-breaker state of a technical capability."""

    __tablename__ = "capability_health"

    capability_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_failure_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_circuit_broken: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    details_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )


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
            if any(
                secret_kw in k_lower
                for secret_kw in ("password", "token", "secret", "auth_b64", "pass", "field1489", "1489")
            ):
                sanitized[k] = "***REDACTED***"
            else:
                sanitized[k] = sanitize_secrets(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_secrets(item) for item in data]
    elif isinstance(data, str):
        return sanitize_secret_text(data)
    return data


@event.listens_for(CommandRecord, "before_insert")
@event.listens_for(CommandRecord, "before_update")
def _sanitize_command_record_listener(mapper: Any, connection: Any, target: CommandRecord) -> None:
    target.target_json = sanitize_secrets(target.target_json or {})
    if target.params_json:
        target.params_json = sanitize_secrets(target.params_json)
    if target.result_json:
        target.result_json = sanitize_secrets(target.result_json)
    if target.error_message:
        target.error_message = sanitize_secret_text(target.error_message)


@event.listens_for(CaseDecisionRecord, "before_insert")
@event.listens_for(CaseDecisionRecord, "before_update")
def _sanitize_case_decision_listener(mapper: Any, connection: Any, target: CaseDecisionRecord) -> None:
    target.case_frame_json = sanitize_secrets(target.case_frame_json or {})
    target.decision_json = sanitize_secrets(target.decision_json or {})


@event.listens_for(WorkflowPlanRecord, "before_insert")
@event.listens_for(WorkflowPlanRecord, "before_update")
def _sanitize_workflow_plan_listener(mapper: Any, connection: Any, target: WorkflowPlanRecord) -> None:
    target.plan_json = sanitize_secrets(target.plan_json or {})


@event.listens_for(ActionPlanRecord, "before_insert")
@event.listens_for(ActionPlanRecord, "before_update")
def _sanitize_action_plan_listener(mapper: Any, connection: Any, target: ActionPlanRecord) -> None:
    target.plan_json = sanitize_secrets(target.plan_json or {})


@event.listens_for(ServiceCompatibilityDecisionRecord, "before_insert")
@event.listens_for(ServiceCompatibilityDecisionRecord, "before_update")
def _sanitize_compatibility_listener(mapper: Any, connection: Any, target: ServiceCompatibilityDecisionRecord) -> None:
    target.decision_json = sanitize_secrets(target.decision_json or {})


@event.listens_for(ServiceRoutingFeedbackRecord, "before_insert")
@event.listens_for(ServiceRoutingFeedbackRecord, "before_update")
def _sanitize_service_routing_feedback_listener(
    mapper: Any,
    connection: Any,
    target: ServiceRoutingFeedbackRecord,
) -> None:
    if target.notes:
        target.notes = sanitize_secret_text(target.notes)


@event.listens_for(RedirectPlanRecord, "before_insert")
@event.listens_for(RedirectPlanRecord, "before_update")
def _sanitize_redirect_plan_listener(mapper: Any, connection: Any, target: RedirectPlanRecord) -> None:
    target.plan_json = sanitize_secrets(target.plan_json or {})
    target.rendered_public_comment = sanitize_secret_text(target.rendered_public_comment)


@event.listens_for(RedirectFeedbackRecord, "before_insert")
@event.listens_for(RedirectFeedbackRecord, "before_update")
def _sanitize_redirect_feedback_listener(mapper: Any, connection: Any, target: RedirectFeedbackRecord) -> None:
    if target.notes:
        target.notes = sanitize_secret_text(target.notes)


@event.listens_for(ActionPreflightRecord, "before_insert")
@event.listens_for(ActionPreflightRecord, "before_update")
def _sanitize_action_preflight_listener(mapper: Any, connection: Any, target: ActionPreflightRecord) -> None:
    target.details_json = sanitize_secrets(target.details_json or {})
    if target.error_message:
        target.error_message = sanitize_secret_text(target.error_message)


@event.listens_for(CaseFeedbackRecord, "before_insert")
@event.listens_for(CaseFeedbackRecord, "before_update")
def _sanitize_case_feedback_listener(mapper: Any, connection: Any, target: CaseFeedbackRecord) -> None:
    if target.corrected_frame_json:
        target.corrected_frame_json = sanitize_secrets(target.corrected_frame_json)
    if target.notes:
        target.notes = sanitize_secret_text(target.notes)


@event.listens_for(PlanFeedbackRecord, "before_insert")
@event.listens_for(PlanFeedbackRecord, "before_update")
def _sanitize_plan_feedback_listener(mapper: Any, connection: Any, target: PlanFeedbackRecord) -> None:
    if target.notes:
        target.notes = sanitize_secret_text(target.notes)


@event.listens_for(ExecutionFeedbackRecord, "before_insert")
@event.listens_for(ExecutionFeedbackRecord, "before_update")
def _sanitize_execution_feedback_listener(
    mapper: Any,
    connection: Any,
    target: ExecutionFeedbackRecord,
) -> None:
    target.result_json = sanitize_secrets(target.result_json or {})


@event.listens_for(CapabilityHealthRecord, "before_insert")
@event.listens_for(CapabilityHealthRecord, "before_update")
def _sanitize_capability_health_listener(
    mapper: Any,
    connection: Any,
    target: CapabilityHealthRecord,
) -> None:
    target.details_json = sanitize_secrets(target.details_json or {})
