from typing import Any

from .base import BaseRule, RuleDecision


class PhysicalDeliveryRule(BaseRule):
    """
    Правило: Физическая доставка оборудования в каб. 112 (Статус 48).
    """

    def __init__(self, priority: int = 20):
        super().__init__(priority=priority)

    @property
    def name(self) -> str:
        return "PhysicalDeliveryRule"

    def evaluate(
        self,
        task: dict[str, Any],
        diag: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        redirect_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> RuleDecision | None:
        name = (task.get("Name") or "").lower()
        desc = (task.get("Description") or "").lower()
        user_text = f"{name} {desc}".strip()

        # 1. Прямое обещание принести устройство
        is_direct_delivery = any(w in user_text for w in [
            "принесу к вам", "привезем", "принесем", "принесу компьютер", "принести компьютер",
            "принести системный", "принести в 112", "принести устройство", "принесу ноутбук"
        ])
        if is_direct_delivery:
            is_pc = any(w in user_text for w in ["пк", "компьютер", "системн", "блок", "ноутбук"])
            key = "bring_pc_112" if is_pc else "bring_device_112"
            comment = (
                "Ждем вас в АБК 3, каб. 112 с ПК.\nПо вопросам звоните на номер 49-87."
                if is_pc else
                "Требуется принести устройство в АБК-3, 112 кабинет для диагностики.\nПо вопросам звоните на номер 49-87."
            )
            return RuleDecision(
                template_key=key,
                name="Ждем в АБК-3 с ПК" if is_pc else "Принести устройство в каб. 112 (Диагностика/Ремонт)",
                status_id=48,
                status_name="Ожидание устройства",
                expenses=10,
                comment=comment,
            )

        # 2. Аппаратный ремонт, установка видеокарт, сгоревшие компоненты
        if any(w in user_text for w in [
            "диагностика пк", "новый процессор", "новый системный", "замена диска",
            "замена hdd", "замена ssd", "черный экран", "пищит компьютер", "замена памяти",
            "аппаратный ремонт", "сгорел", "задымился", "второй монитор", "видеокарт"
        ]):
            return RuleDecision(
                template_key="bring_device_112",
                name="Принести устройство в каб. 112 (Диагностика/Ремонт)",
                status_id=48,
                status_name="Ожидание устройства",
                expenses=10,
                comment="Требуется принести устройство в АБК-3, 112 кабинет для диагностики.\nПо вопросам звоните на номер 49-87.",
            )

        return None
