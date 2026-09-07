"""Versioned, fail-closed decisioning contracts."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Evidence(StrictModel):
    source: Literal["structured", "parser", "llm", "rule", "worker"]
    field: str
    code: str
    detail: str | None = None
    span: str | None = None


class ValidationError(StrictModel):
    field: str
    code: Literal[
        "missing",
        "too_short",
        "invalid_characters",
        "stopword",
        "invalid_format",
        "conflict",
    ]
    message: str


class PersonCandidate(StrictModel):
    surname: str = ""
    name: str = ""
    patronymic: str = ""
    title: str = ""
    department: str = ""
    company: str = ""
    phone: str = ""
    pc_name: str = ""


class ValidPerson(StrictModel):
    surname: str = Field(min_length=2, max_length=100)
    name: str = Field(min_length=2, max_length=100)
    patronymic: str = Field(default="", max_length=100)
    title: str = Field(default="", max_length=255)
    department: str = Field(default="", max_length=255)
    company: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=100)
    pc_name: str = Field(default="", max_length=100)


class TicketFacts(StrictModel):
    schema_version: Literal[1] = 1
    task_id: int | None = None
    service_id: int | None = None
    service_parent_id: int | None = None
    name: str = ""
    description: str = ""
    service_name: str = ""
    person: PersonCandidate | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class ExtractedPersonCandidate(StrictModel):
    surname: str | None = None
    name: str | None = None
    patronymic: str | None = None
    title: str | None = None
    department: str | None = None
    company: str | None = None
    phone: str | None = None
    pc_name: str | None = None


class ExtractedTicketFacts(StrictModel):
    """Schema-constrained LLM output. It is evidence, never authorization."""

    schema_version: Literal[1] = 1
    person: ExtractedPersonCandidate | None = None
    pc_name: str | None = None
    printer_address: str | None = None
    file_path: str | None = None
    clarification_answer: str | None = None
    issue_summary: str | None = None
    comments_count_analyzed: int = 0
    evidence: list[Evidence] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)


class OutcomeBase(StrictModel):
    schema_version: Literal[1] = 1
    rule_key: str
    rule_version: str
    evidence: list[Evidence] = Field(default_factory=list)


class ClarificationRequired(OutcomeBase):
    kind: Literal["clarification"] = "clarification"
    outcome_key: str
    missing_fields: list[str] = Field(default_factory=list)
    invalid_fields: list[str] = Field(default_factory=list)
    context: dict[str, str] = Field(default_factory=dict)


class ResolutionProposed(OutcomeBase):
    kind: Literal["resolution"] = "resolution"
    outcome_key: str
    target_status_id: Literal[27, 29, 30, 35, 48] | None = None
    context: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateUserParameters(StrictModel):
    surname: str
    name: str
    patronymic: str = ""
    company: str = ""
    department: str = ""
    phone: str = ""
    pc_name: str = ""
    title: str = ""


class ActionProposed(OutcomeBase):
    kind: Literal["action"] = "action"
    outcome_key: str
    action: Literal["create_user", "grant_wlan", "install_printer", "apply_triage"]
    parameters: CreateUserParameters | dict[str, str]
    risk_level: Literal[0, 1, 2, 3]
    requires_approval: bool = True


class ManualReviewRequired(OutcomeBase):
    kind: Literal["manual_review"] = "manual_review"
    reason: str


class NoMatch(OutcomeBase):
    kind: Literal["no_match"] = "no_match"


DecisionOutcome = Annotated[
    ClarificationRequired | ActionProposed | ResolutionProposed | ManualReviewRequired | NoMatch,
    Field(discriminator="kind"),
]

DecisionOutcomeAdapter = TypeAdapter(DecisionOutcome)
