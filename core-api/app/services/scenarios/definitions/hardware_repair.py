"""Scenario: Physical Device Delivery & Hardware Repair (physical_device)."""

from __future__ import annotations

import logging
from typing import Any

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.definitions.base_definition import (
    contains_any,
    default_standard_in_work_outcome,
    extract_ticket_text,
)
from shared.domain import (
    DecisionOutcome,
    Evidence,
    ResolutionProposed,
    ScenarioDefinition,
    ScenarioMatch,
)

logger = logging.getLogger("core_api.scenarios.hardware_repair")

HARDWARE_SERVICE_IDS = {88, 112, 113}


def evaluate_physical_delivery(task: dict[str, Any], context: ScenarioContext) -> DecisionOutcome:
    name = str(task.get("Name") or "").lower()
    desc = str(task.get("Description") or "").lower()
    user_text = f"{name} {desc}".strip()

    comments_text = " ".join(
        str(c.get("Comment") or c.get("comment") or c.get("Text") or "")
        for c in (context.comments or [])
        if isinstance(c, dict)
    ).lower()
    full_text = f"{user_text} {comments_text}".strip()

    # 1. Устройство уже доставлено в 112 каб
    is_already_delivered = any(w in full_text for w in [
        "находится в 112", "в 112 кабинете", "в 112 каб",
        "принес в 112", "принесла в 112", "принесли в 112", "занес в 112",
        "оставил в 112", "уже в 112", "передал в 112", "стоит в 112", "лежит в 112"
    ])
    if is_already_delivered:
        return ResolutionProposed(
            rule_key="device.delivery",
            rule_version="2",
            outcome_key="device_in_112",
            target_status_id=27,
            evidence=[Evidence(source="rule", field="location", code="already_in_112")],
        )

    # 2. Аппаратная неисправность / диагностика
    is_hardware = any(w in user_text for w in [
        "диагностика пк", "диагностика компьютера", "новый системный",
        "замена диска", "замена hdd", "замена ssd", "черный экран", "пищит компьютер",
        "замена памяти", "аппаратный ремонт", "сгорел", "задымился", "второй монитор",
        "видеокарт", "материнск", "блок питания", "кулер", "замена термопасты",
        "разбит экран", "треснул", "уронили", "не загорается экран", "поврежден корпус",
    ])
    is_pc = any(w in user_text for w in ["пк", "компьютер", "комп", "системн", "системник", "блок", "ноутбук", "моноблок", "экран"])
    device_name = "ноутбук" if any(w in user_text for w in ["ноутбук", "ноутах", "laptop"]) else "системный блок"

    if is_hardware:
        return ResolutionProposed(
            rule_key="device.delivery",
            rule_version="2",
            outcome_key="hardware_repair" if is_pc else "bring_device_112",
            target_status_id=48,
            context={"device_name": device_name},
            evidence=[Evidence(source="rule", field="defect", code="hardware_issue")],
        )

    return ResolutionProposed(
        rule_key="device.delivery",
        rule_version="2",
        outcome_key="bring_device_112",
        target_status_id=48,
        context={"device_name": device_name},
        evidence=[Evidence(source="rule", field="device", code="generic_delivery")],
    )


class HardwareRepairScenario(Scenario):
    """Сценарий доставки оборудования в Каб. 112 (Статус 48)."""

    definition = ScenarioDefinition(
        key="physical_device",
        version=1,
        risk_level=1,
    )

    def match(self, context: ScenarioContext) -> ScenarioMatch:
        text = extract_ticket_text(context)
        task = context.task
        sid = task.get("ServiceId") or task.get("service_id")
        if sid is not None and int(sid) in HARDWARE_SERVICE_IDS:
            return ScenarioMatch(
                scenario_key=self.definition.key,
                scenario_version=self.definition.version,
                matched=True,
                score=0.95,
                reason=f"service_id:{sid}",
            )

        keywords = ("системный блок", "ноутбук", "монитор", "ремонт оборудования", "каб. 112", "кабинет 112")
        found = [kw for kw in keywords if kw in text]
        if found:
            return ScenarioMatch(
                scenario_key=self.definition.key,
                scenario_version=self.definition.version,
                matched=True,
                score=min(1.0, 0.70 + 0.05 * len(found)),
                reason=",".join(found),
            )

        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=False,
            score=0.0,
            reason=None,
        )

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        return evaluate_physical_delivery(context.task, context)
