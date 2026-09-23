"""Scenario: Service Redirect (redirect)."""

from __future__ import annotations

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.service_catalog import get_root_name
from app.services.template_engine import detect_service_redirect
from shared.domain import (
    DecisionOutcome,
    Evidence,
    ResolutionProposed,
    ScenarioDefinition,
    ScenarioMatch,
)


class ServiceRedirectScenario(Scenario):
    """Сценарий перенаправления ошибочно поданных заявок (Статус 30)."""

    definition = ScenarioDefinition(
        key="redirect",
        version=1,
        risk_level=1,
        allowed_actions=["apply_triage"],
    )

    def match(self, context: ScenarioContext) -> ScenarioMatch:
        redirect = detect_service_redirect(context.task)
        if redirect:
            return ScenarioMatch(
                scenario_key=self.definition.key,
                scenario_version=self.definition.version,
                matched=True,
                score=1.0,
                reason=str(redirect.get("reason") or "catalog_redirect"),
            )
        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=False,
            score=0.0,
            reason=None,
        )

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        redirect = detect_service_redirect(context.task)
        target_name = (redirect.get("target_service_name") if redirect else None) or "соответствующий раздел"
        return ResolutionProposed(
            rule_key="service.redirect",
            rule_version="2",
            outcome_key="wrong_service",
            target_status_id=30,
            context={"target_service": target_name},
            evidence=[
                Evidence(
                    source="rule",
                    field="service_id",
                    code="wrong_catalog_root",
                    detail=f"Redirected to {target_name}",
                )
            ],
            metadata=redirect or {},
        )
