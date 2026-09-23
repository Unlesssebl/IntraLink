"""Scenario: User Creation in Active Directory (create_user)."""

from __future__ import annotations

import logging
from typing import Any

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.definitions.base_definition import extract_ticket_text
from app.utils.ad_utils import (
    extract_person_candidate_from_task,
    generate_sam_account_name,
)
from shared.domain import (
    ActionProposed,
    ClarificationRequired,
    CreateUserParameters,
    DecisionOutcome,
    Evidence,
    FactRequirement,
    ManualReviewRequired,
    PersonCandidate,
    ScenarioDefinition,
    ScenarioMatch,
    validate_person_candidate,
)

logger = logging.getLogger("core_api.scenarios.create_user")

USER_CREATION_SERVICE_IDS = {42, 53, 54, 55, 124, 104, 186}

USER_CREATION_PHRASES = (
    "создать учет",
    "создать учёт",
    "создание учет",
    "создание учёт",
    "создать пользователя",
    "создание пользователя",
    "завести пользователя",
    "завести сотрудника",
    "новый пользователь",
    "новый сотрудник",
    "создание уз",
    "создать уз",
    "новая учетная запись",
    "заявка на создание пользователя",
    "заявка на создание учетной записи",
)

USER_CREATION_EXCLUSIONS = (
    "сброс парол",
    "сбросить парол",
    "забыл парол",
    "забыла парол",
    "заблокирован",
    "wlan",
    "wi-fi",
    "wifi",
)


class CreateUserScenario(Scenario):
    """Сценарий создания учетной записи пользователя в Active Directory."""

    definition = ScenarioDefinition(
        key="create_user",
        version=1,
        risk_level=2,
        allowed_actions=["create_user"],
        clarification_outcome_key="account_details_invalid",
        success_outcome_key="user_created",
        required_facts=[
            FactRequirement(key="surname", clarification_key="clarify_surname"),
            FactRequirement(key="name", clarification_key="clarify_name"),
            FactRequirement(key="company", clarification_key="clarify_company"),
            FactRequirement(key="department", clarification_key="clarify_department"),
            FactRequirement(key="title", clarification_key="clarify_title"),
        ],
    )

    def match(self, context: ScenarioContext) -> ScenarioMatch:
        text = extract_ticket_text(context)
        task = context.task
        sid = task.get("ServiceId") or task.get("service_id")
        pid = task.get("ServiceParentId") or task.get("service_parent_id")
        ttid = task.get("TaskTypeId") or task.get("task_type_id")

        # 1. Проверяем исключения (сброс пароля, wifi)
        if any(ex in text for ex in USER_CREATION_EXCLUSIONS):
            return ScenarioMatch(
                scenario_key=self.definition.key,
                scenario_version=self.definition.version,
                matched=False,
                score=0.0,
                reason="exclusion_matched",
            )

        # 2. Проверка по каталогу / типам
        sid_int = int(sid) if sid is not None and str(sid).isdigit() else None
        if sid_int in USER_CREATION_SERVICE_IDS or (pid == 42 and sid_int != 63):
            return ScenarioMatch(
                scenario_key=self.definition.key,
                scenario_version=self.definition.version,
                matched=True,
                score=0.96,
                reason=f"catalog_service_{sid_int}",
            )

        if ttid is not None and str(ttid) == "1018":
            return ScenarioMatch(
                scenario_key=self.definition.key,
                scenario_version=self.definition.version,
                matched=True,
                score=0.96,
                reason="task_type_1018",
            )

        # 3. Проверка по фразам
        for phrase in USER_CREATION_PHRASES:
            if phrase in text:
                return ScenarioMatch(
                    scenario_key=self.definition.key,
                    scenario_version=self.definition.version,
                    matched=True,
                    score=0.94,
                    reason=f"phrase:{phrase}",
                )

        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=False,
            score=0.0,
            reason=None,
        )

    def requirements(self, context: ScenarioContext) -> tuple[FactRequirement, ...]:
        return tuple(self.definition.required_facts)

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        task = context.task
        if task.get("_fact_extraction_manual_review"):
            return ManualReviewRequired(
                rule_key="credentials.user_creation",
                rule_version="2",
                reason=str(task["_fact_extraction_manual_review"]),
            )

        candidate = (
            PersonCandidate.model_validate(task["_extracted_person"])
            if task.get("_extracted_person")
            else extract_person_candidate_from_task(task)
        )
        validation = validate_person_candidate(candidate)
        if not validation.valid:
            invalid_fields = sorted({error.field for error in validation.errors})
            return ClarificationRequired(
                rule_key="credentials.user_creation",
                rule_version="2",
                outcome_key="account_details_invalid",
                invalid_fields=invalid_fields,
                context={"invalid_fields": ", ".join(invalid_fields)},
                evidence=[
                    Evidence(
                        source="rule",
                        field=error.field,
                        code=error.code,
                        detail=error.message,
                    )
                    for error in validation.errors
                ],
            )

        person = validation.person
        assert person is not None
        missing_business_fields = [
            field for field in ("company", "department", "title")
            if not getattr(person, field).strip()
        ]
        if missing_business_fields:
            return ClarificationRequired(
                rule_key="credentials.user_creation",
                rule_version="2",
                outcome_key="account_details_invalid",
                missing_fields=missing_business_fields,
                context={"invalid_fields": ", ".join(missing_business_fields)},
                evidence=[
                    Evidence(source="rule", field=field, code="missing")
                    for field in missing_business_fields
                ],
            )

        return ActionProposed(
            rule_key="credentials.user_creation",
            rule_version="2",
            outcome_key="create_user_proposed",
            action="create_user",
            parameters=CreateUserParameters(**person.model_dump()),
            risk_level=self.definition.risk_level,
            requires_approval=True,
            evidence=[
                Evidence(source="rule", field="fio", code="valid_candidate"),
                Evidence(
                    source="rule",
                    field="sam_account_name",
                    code="generated",
                    detail=generate_sam_account_name(person),
                ),
            ],
        )
