"""Scenario: SMB File Lock Release (file_lock)."""

from __future__ import annotations

import re
from typing import Any

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.definitions.base_definition import (
    contains_any,
    default_standard_in_work_outcome,
    extract_ticket_text,
)
from shared.domain import (
    ClarificationRequired,
    DecisionOutcome,
    Evidence,
    ResolutionProposed,
    ScenarioDefinition,
    ScenarioMatch,
)

FILE_LOCK_KEYWORDS = (
    "файл занят",
    "файл заблокирован",
    "открыт другим",
    "сетевой файл",
    "занят другим",
    "занята другим",
    "заблокирован другим",
    "якобы я им пользуюсь",
    "обменный exle",
    "обменный excel",
)


class FileLockScenario(Scenario):
    """Сценарий сброса зависшей сессии / файловой блокировки SMB."""

    definition = ScenarioDefinition(
        key="file_lock",
        version=1,
        risk_level=1,
    )

    def match(self, context: ScenarioContext) -> ScenarioMatch:
        matcher = contains_any(*FILE_LOCK_KEYWORDS)
        matched, score, reason = matcher(context)
        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=matched,
            score=score,
            reason=reason or None,
        )

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        task = context.task
        name = str(task.get("Name") or "").lower()
        desc = str(task.get("Description") or "").lower()
        extracted_path = context.facts.valid_value("file_path")
        has_path = (
            extracted_path
            or re.search(r"(\\\\[a-zA-Z0-9_\-\.]+\\[^\s]+|[a-zA-Z]:\\[^\s]+)", desc + " " + name)
        )

        if not has_path:
            return ClarificationRequired(
                rule_key="smb.file_lock",
                rule_version="2",
                outcome_key="file_lock_smb",
                missing_fields=["file_path"],
                evidence=[
                    Evidence(
                        source="rule",
                        field="file_path",
                        code="path_missing",
                        detail="SMB lock detected but UNC/local path is not specified",
                    )
                ],
            )

        return ResolutionProposed(
            rule_key="smb.file_lock",
            rule_version="2",
            outcome_key="file_lock_smb_in_progress",
            target_status_id=27,
            evidence=[
                Evidence(
                    source="rule",
                    field="file_path",
                    code="path_provided",
                    detail=str(extracted_path or "path detected in text"),
                )
            ],
        )
