"""Typed contracts for scenario-driven ticket execution."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
import uuid

from pydantic import Field, model_validator

from shared.domain.models import DecisionOutcome, StrictModel


class FactState(str, Enum):
    MISSING = "missing"
    VALID = "valid"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"
    STALE = "stale"


class FactSource(str, Enum):
    STRUCTURED_FIELD = "structured_field"
    DIRECTORY = "directory"
    DIAGNOSTIC = "diagnostic"
    COMMENT = "comment"
    PARSER = "parser"
    LLM = "llm"
    OPERATOR = "operator"


class FactSensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PERSONAL = "personal"
    SECRET = "secret"


class FactObservation(StrictModel):
    schema_version: Literal[1] = 1
    key: str = Field(min_length=1, max_length=100)
    value: Any = None
    state: FactState
    source: FactSource
    source_ref: str = Field(min_length=1, max_length=255)
    evidence_span: str | None = Field(default=None, max_length=2000)
    sensitivity: FactSensitivity = FactSensitivity.INTERNAL
    observed_at: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_grounding_for_values(self) -> "FactObservation":
        if self.state is FactState.VALID and self.value is None:
            raise ValueError("valid fact observation requires a value")
        if self.source in {FactSource.COMMENT, FactSource.PARSER, FactSource.LLM}:
            if self.value is not None and not (self.evidence_span or "").strip():
                raise ValueError("text-derived fact observation requires evidence_span")
        return self


class ResolvedFact(StrictModel):
    schema_version: Literal[1] = 1
    key: str
    value: Any = None
    state: FactState
    selected_source: FactSource | None = None
    selected_source_ref: str | None = None
    observations: list[FactObservation] = Field(default_factory=list)
    conflict_reason: str | None = None


class FactBag(StrictModel):
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    facts: dict[str, ResolvedFact] = Field(default_factory=dict)

    def valid_value(self, key: str, default: Any = None) -> Any:
        fact = self.facts.get(key)
        if fact is None or fact.state is not FactState.VALID:
            return default
        return fact.value


class FactRequirement(StrictModel):
    schema_version: Literal[1] = 1
    key: str
    required: bool = True
    required_when: dict[str, Any] = Field(default_factory=dict)
    clarification_key: str | None = None


class ScenarioDefinition(StrictModel):
    schema_version: Literal[1] = 1
    key: str
    version: int = Field(ge=1)
    required_facts: list[FactRequirement] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    risk_level: Literal[0, 1, 2, 3] = 0
    clarification_outcome_key: str | None = None
    success_outcome_key: str | None = None


class ScenarioMatch(StrictModel):
    schema_version: Literal[1] = 1
    scenario_key: str
    scenario_version: int = Field(ge=1)
    matched: bool
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str | None = None


class StepKind(str, Enum):
    # orchestration kinds
    COLLECT = "collect"
    CLARIFY = "clarify"
    DECIDE = "decide"
    APPROVE = "approve"
    DISPATCH = "dispatch"
    WAIT = "wait"
    FINALIZE = "finalize"
    MANUAL_REVIEW = "manual_review"

    # diagnostic kinds
    CHECK = "check"
    ACTION = "action"
    MANUAL = "manual"
    VERIFY = "verify"


class StepStatus(str, Enum):
    NOT_STARTED = "not_started"
    WAITING_INPUT = "waiting_input"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNSUPPORTED = "unsupported"


class PlanStep(StrictModel):
    schema_version: Literal[2] = 2
    id: str
    title: str = ""
    kind: StepKind
    status: StepStatus = StepStatus.NOT_STARTED
    required_for_resolution: bool = False
    capability_id: str | None = None
    executor: Literal["backend", "windows", "engineer"] | None = None
    target_ref: str | None = None
    result_summary: str | None = None
    requires: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    status_source: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    completed_by: str | None = None
    skip_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_step_invariants(self) -> "PlanStep":
        if self.status == StepStatus.COMPLETED:
            if not self.evidence_refs:
                raise ValueError(f"completed step {self.id} must have non-empty evidence_refs")
            if self.kind == StepKind.MANUAL and not (self.completed_by and self.completed_at):
                raise ValueError(f"completed manual step {self.id} requires completed_by and completed_at")
        if self.status == StepStatus.SKIPPED and not self.skip_reason:
            raise ValueError(f"skipped step {self.id} requires skip_reason")
        return self


class LegacyPlanStep(StrictModel):
    schema_version: Literal[1] = 1
    id: str
    kind: Literal[
        "collect",
        "clarify",
        "decide",
        "approve",
        "dispatch",
        "wait",
        "finalize",
        "manual_review",
    ]
    requires: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(StrictModel):
    schema_version: Literal[2] = 2
    scenario_key: str
    scenario_version: int = Field(ge=1)
    fact_revision: int = Field(default=0, ge=0)
    title: str = ""
    phase: Literal[
        "collecting", "ready", "dispatched", "running", "verifying", "completed", "blocked"
    ] = "collecting"
    next_action_description: str = ""
    steps: list[PlanStep] = Field(default_factory=list)


class LegacyExecutionPlan(StrictModel):
    schema_version: Literal[1] = 1
    scenario_key: str
    scenario_version: int = Field(ge=1)
    fact_revision: int = Field(ge=0)
    steps: list[LegacyPlanStep] = Field(default_factory=list)


def parse_execution_plan(
    data: Any,
) -> ExecutionPlan | LegacyExecutionPlan | None:
    if data is None:
        return None
    if isinstance(data, (ExecutionPlan, LegacyExecutionPlan)):
        return data
    if isinstance(data, dict):
        schema_version = data.get("schema_version")
        if schema_version == 1:
            return LegacyExecutionPlan.model_validate(data)
        # If schema_version is 2 or missing, validate as ExecutionPlan v2
        # Unless it only matches LegacyExecutionPlan fields and steps have no v2 fields
        steps = data.get("steps", [])
        has_v2_fields = (
            "phase" in data
            or "title" in data
            or any(
                isinstance(s, dict) and ("title" in s or "status" in s or s.get("schema_version") == 2)
                for s in steps
            )
        )
        if schema_version == 2 or has_v2_fields or not steps:
            return ExecutionPlan.model_validate(data)
        try:
            return LegacyExecutionPlan.model_validate(data)
        except Exception:
            return ExecutionPlan.model_validate(data)
    raise ValueError(f"unsupported execution plan data: {type(data)}")


class DecisionRoutingInfo(StrictModel):
    schema_version: Literal[1] = 1
    selected_score: float = 0.0
    runner_up_score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    is_ambiguous: bool = False


class CandidateOutcome(StrictModel):
    schema_version: Literal[1] = 1
    candidate_id: str
    source: Literal["rule", "rag", "diagnostic", "operator", "fallback"]
    outcome: DecisionOutcome
    evidence_refs: list[str] = Field(default_factory=list)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    can_authorize_action: bool = False
    routing: DecisionRoutingInfo | None = None


class RejectedCandidate(StrictModel):
    candidate_id: str
    reason: str


class SynthesisProposal(StrictModel):
    schema_version: Literal[1] = 1
    selected_candidate_id: str
    used_evidence_refs: list[str] = Field(default_factory=list)
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    response_plan: dict[str, Any] = Field(default_factory=dict)


class DecisionResponse(StrictModel):
    schema_version: Literal[1] = 1
    text: str = Field(default="", max_length=900)
    mode: Literal["template", "llm", "fallback", "none"] = "none"
    state: Literal["valid", "fallback", "invalid"] = "invalid"
    violations: list[str] = Field(default_factory=list)
    used_evidence_refs: list[str] = Field(default_factory=list)


class DecisionGates(StrictModel):
    schema_version: Literal[1] = 1
    can_send_response: bool = False
    can_execute_action: bool = False
    requires_approval: bool = False
    blocked_reasons: list[str] = Field(default_factory=list)


class DecisionEnvelope(StrictModel):
    schema_version: Literal[1, 2] = 2
    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    decision_version: int = Field(default=1, ge=1)
    scenario_key: str
    scenario_version: int = Field(ge=1)
    scenario_title: str | None = None
    facts_revision: int = Field(default=0, ge=0)
    analysis_state: Literal["succeeded", "manual_review", "system_error"] = "succeeded"
    facts_state: Literal["sufficient", "incomplete", "conflicting"] = "sufficient"
    facts_summary: dict[str, Any] = Field(default_factory=dict)
    candidates: list[CandidateOutcome] = Field(default_factory=list)
    outcome: DecisionOutcome
    policy: dict[str, Any] = Field(default_factory=dict)
    response: DecisionResponse = Field(default_factory=DecisionResponse)
    gates: DecisionGates = Field(default_factory=DecisionGates)
    execution_plan: ExecutionPlan | LegacyExecutionPlan | None = None
    internal_summary: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    routing: DecisionRoutingInfo | None = None
    clarifications: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal[
        "proposed",
        "waiting_answer",
        "waiting_approval",
        "executing",
        "verified",
        "manual_review",
        "system_error",
    ] = "proposed"

    @model_validator(mode="before")
    @classmethod
    def _normalize_envelope_inputs(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Backward compatibility for response_draft / requires_approval
            if "response_draft" in data and "response" not in data:
                draft = data.pop("response_draft")
                data["response"] = DecisionResponse(
                    text=str(draft or ""),
                    mode="template",
                    state="fallback",
                )
            elif "response_draft" in data:
                data.pop("response_draft")
            if "gates" not in data:
                outcome = data.get("outcome")
                is_action = (
                    getattr(outcome, "kind", None) == "action"
                    or (isinstance(outcome, dict) and outcome.get("kind") == "action")
                )
                req_app = data.pop("requires_approval", False)
                data["gates"] = DecisionGates(
                    can_send_response=True,
                    can_execute_action=is_action,
                    requires_approval=bool(req_app),
                )
            elif "requires_approval" in data:
                req_app = data.pop("requires_approval")
                gates = data["gates"]
                if isinstance(gates, dict):
                    gates.setdefault("requires_approval", bool(req_app))
                elif hasattr(gates, "requires_approval") and not getattr(gates, "requires_approval", False):
                    gates.requires_approval = bool(req_app)
            if not data.get("decision_id"):
                data["decision_id"] = str(uuid.uuid4())
            # Compatibility with diagnostic_plan alias
            if "diagnostic_plan" in data and "execution_plan" not in data:
                data["execution_plan"] = data.pop("diagnostic_plan")
            elif "diagnostic_plan" in data:
                data.pop("diagnostic_plan")
            if "execution_plan" in data and data["execution_plan"] is not None:
                data["execution_plan"] = parse_execution_plan(data["execution_plan"])
        return data

    @model_validator(mode="after")
    def _populate_scenario_title(self) -> "DecisionEnvelope":
        if not self.scenario_title and self.scenario_key:
            self.scenario_title = get_scenario_display_name(
                self.scenario_key, version=self.scenario_version
            )
        return self

    @property
    def response_draft(self) -> str:
        return self.response.text if self.response else ""

    @property
    def diagnostic_plan(self) -> ExecutionPlan | LegacyExecutionPlan | None:
        """Compatibility accessor for UI/Inspector without duplicating JSON serialization."""
        return self.execution_plan


SCENARIO_DISPLAY_NAMES: dict[str, str] = {
    "install_printer": "Установка принтера / МФУ",
    "printer_installation": "Установка принтера / МФУ (legacy)",
    "printer_hardware_service": "Сервисный ремонт оргтехники",
    "printer_scan_failure": "Диагностика сетевого сканирования",
    "printer_print_failure": "Устранение сбоя очереди печати",
    "peripheral_setup": "Установка и подключение периферии",
    "peripheral_diagnostics": "Диагностика периферийных устройств",
    "pc_performance": "Диагностика производительности ПК",
    "network_diagnostics": "Диагностика сетевого подключения",
    "os_reinstallation": "Переустановка операционной системы",
    "create_user": "Создание учётной записи (AD)",
    "user_creation": "Создание учётной записи (AD legacy)",
    "grant_wlan": "Доступ к корпоративному Wi-Fi",
    "wlan_access": "Доступ к корпоративному Wi-Fi (legacy)",
    "redirect": "Перенаправление в целевой сервис",
    "service_redirect": "Перенаправление в целевой сервис (legacy)",
    "offline_host": "Диагностика недоступного ПК",
    "file_lock": "Снятие блокировки файла (SMB)",
    "physical_device": "Ремонт и перемещение оборудования",
    "hardware_repair": "Ремонт и перемещение оборудования (legacy)",
    "rag_consultation": "Консультация по базе знаний (RAG)",
    "duplicate_task": "Отмена заявки-дубликата",
    "duplicate": "Отмена заявки-дубликата",
    "consultation": "Стандартная обработка 1-й линией",
}

SCENARIO_SHORT_NAMES: dict[str, str] = {
    "install_printer": "Установка МФУ",
    "printer_installation": "Установка МФУ",
    "printer_hardware_service": "Ремонт МФУ",
    "printer_scan_failure": "Сбой сканирования",
    "printer_print_failure": "Сбой печати",
    "peripheral_setup": "Периферия",
    "peripheral_diagnostics": "Сбой периферии",
    "pc_performance": "Тормоза ПК",
    "network_diagnostics": "Сбой сети",
    "os_reinstallation": "Переустановка ОС",
    "create_user": "Создание УЗ",
    "user_creation": "Создание УЗ",
    "grant_wlan": "Wi-Fi доступ",
    "wlan_access": "Wi-Fi доступ",
    "redirect": "Перенаправление",
    "service_redirect": "Перенаправление",
    "offline_host": "ПК офлайн",
    "file_lock": "Блокировка файла",
    "physical_device": "Каб. 112 (Ремонт)",
    "hardware_repair": "Каб. 112 (Ремонт)",
    "rag_consultation": "База знаний",
    "duplicate_task": "Дубликат",
    "duplicate": "Дубликат",
    "consultation": "Консультация",
}

SUPPORTED_SCENARIO_KEYS: tuple[str, ...] = tuple(SCENARIO_DISPLAY_NAMES.keys())


def get_scenario_display_name(
    key: str | None,
    version: int | None = None,
    short: bool = False,
) -> str:
    """Возвращает понятное русскоязычное название сценария принятия решений."""
    if not key:
        return "Не определен" if not short else "—"
    clean_key = str(key).strip()
    source_map = SCENARIO_SHORT_NAMES if short else SCENARIO_DISPLAY_NAMES
    name = source_map.get(clean_key)
    if not name:
        normalized = clean_key.replace("_", " ").replace("-", " ")
        name = normalized.capitalize() if not short else clean_key
    if version and version > 1 and not short:
        return f"{name} (v{version})"
    return name

