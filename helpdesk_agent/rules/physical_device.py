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
                "Ждем вас в АБК 3, каб. 112 с ПК.\nЕсли возникнут вопросы, напишите в комментариях к этой заявке."
                if is_pc else
                "Требуется принести устройство в АБК-3, 112 кабинет для диагностики.\nЕсли возникнут вопросы, напишите в комментариях к этой заявке."
            )
            return RuleDecision(
                template_key=key,
                name="Ждем в АБК-3 с ПК" if is_pc else "Принести устройство в каб. 112 (Диагностика/Ремонт)",
                status_id=48,
                status_name="Ожидание устройства",
                expenses=10,
                comment=comment,
            )

        # 2. Аппаратный ремонт, установка комплектующих, сгоревшие компоненты
        is_hardware_issue = any(w in user_text for w in [
            "диагностика пк", "диагностика компьютера", "новый процессор", "новый системный",
            "замена диска", "замена hdd", "замена ssd", "черный экран", "пищит компьютер",
            "замена памяти", "аппаратный ремонт", "сгорел", "задымился", "второй монитор",
            "видеокарт", "материнск", "блок питания", "кулер", "замена термопасты"
        ])
        if is_hardware_issue:
            is_pc = any(w in user_text for w in ["пк", "компьютер", "системн", "блок", "ноутбук"])
            return RuleDecision(
                template_key="hardware_repair" if is_pc else "bring_device_112",
                name="Обслуживание и ремонт ПК в 112 каб." if is_pc else "Принести устройство в каб. 112 (Диагностика/Ремонт)",
                status_id=48,
                status_name="Ожидание устройства",
                expenses=10,
                comment=(
                    "Приносите системный блок в АБК 3, 112 каб. на диагностику, обслуживание и настройку. О времени визита вы можете написать в комментариях к этой заявке."
                    if is_pc else
                    "Требуется принести устройство в АБК-3, 112 кабинет для диагностики.\nЕсли возникнут вопросы, напишите в комментариях к этой заявке."
                ),
            )

        # 3. Обслуживание ПК, проблемы быстродействия, зависания, актирование, чистка и переустановка ОС
        service_id = task.get("ServiceId")
        type_id = task.get("TypeId")
        is_pc_service = (service_id in (32, 31) or type_id == 1012)  # Компьютеры и ноутбуки / Ремонт компов

        # Проверка наличия объекта ПК / железа в тексте
        has_pc_object = any(w in user_text for w in [
            "пк", "компьютер", "комп", "системн", "системник", "блок", "ноутбук", "моноблок",
            "windows", "виндовс", "железо", "машина"
        ]) or is_pc_service

        # Исключение: если жалоба сугубо на прикладную программу / сетевой ресурс без деградации самого ПК
        is_pure_app_issue = any(w in user_text for w in [
            "1с", "1c", "упп", "erp", "зуп", "directum", "директум", "outlook",
            "принтер", "мфу", "сканер", "интернет", "сайт", "браузер", "wifi", "вайфай"
        ]) and not any(w in user_text for w in ["компьютер", "системн", "ноутбук", "сам пк", "весь компьютер"])

        if is_pure_app_issue and not is_pc_service:
            return None

        is_maintenance_issue = any(w in user_text for w in [
            "тормозит", "зависает", "медленно работает", "быстродействие",
            "прогрузить", "актировать", "актирование", "обслуживание",
            "чистка", "прочистить", "пыл", "греется", "шумит", "переустановка",
            "переустановить", "синий экран", "bsod", "не включается", "не запускается",
            "выключается сам", "апгрейд", "ремонт"
        ])

        if has_pc_object and is_maintenance_issue:
            is_notebook = any(w in user_text for w in ["ноутбук", "ноутах"])
            device_name = "ноутбук" if is_notebook else "системный блок"
            return RuleDecision(
                template_key="hardware_repair",
                name="Обслуживание и ремонт ПК в 112 каб.",
                status_id=48,
                status_name="Ожидание устройства",
                expenses=10,
                comment=f"Приносите {device_name} в АБК 3, 112 каб. на диагностику, обслуживание и настройку. О времени визита вы можете написать в комментариях к этой заявке.",
            )

        return None
