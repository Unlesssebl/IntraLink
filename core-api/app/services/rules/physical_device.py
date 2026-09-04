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

        # Извлекаем текст комментариев, если они переданы в context
        comments_history = (context or {}).get("comments_history") or []
        comments_text = " ".join(
            (c.get("Comments") or c.get("Comment") or c.get("Text") or "")
            for c in comments_history
        ).lower()
        full_text = f"{user_text} {comments_text}".strip()

        # 0. Устройство уже фактически доставлено / находится в 112 кабинете
        # Заявитель подтвердил сдачу ПК -> перевод в статус 27 ("В работе")
        is_already_delivered = any(w in full_text for w in [
            "находится в 112", "находится в каб. 112", "в 112 кабинете", "в 112 каб",
            "принес в 112", "принесла в 112", "принесли в 112", "занес в 112", "занесла в 112",
            "оставил в 112", "оставила в 112", "оставили в 112", "у вас в 112", "уже в 112",
            "передал в 112", "передала в 112", "принес пк", "принесла пк", "принес системник",
            "принесла системник", "принес компьютер", "принесла компьютер", "принес ноутбук",
            "принесла ноутбук", "стоит в 112", "лежит в 112", "пк в 112", "компьютер в 112"
        ])
        if is_already_delivered:
            is_notebook = any(w in full_text for w in ["ноутбук", "ноутах", "laptop"])
            device_name = "ноутбук" if is_notebook else "системный блок"
            return RuleDecision(
                template_key="device_delivered_in_work",
                rule_type="hardware_repair",
                name="Ноутбук принят в 112 каб. (В работе)" if is_notebook else "ПК принят в 112 каб. (В работе)",
                status_id=27,
                status_name="В работе",
                expenses=10,
                comment=f"{device_name.capitalize()} принят в 112 кабинете на диагностику и обслуживание. Приступаю к работе.",
            )

        # 1. Прямое обещание принести устройство
        is_direct_delivery = any(w in user_text for w in [
            "принесу к вам", "привезем", "принесем", "принесу компьютер", "принести компьютер",
            "принести системный", "принести в 112", "принести устройство", "принесу ноутбук"
        ])
        if is_direct_delivery:
            is_pc = any(w in user_text for w in ["пк", "компьютер", "комп", "системн", "системник", "блок", "ноутбук", "моноблок"])
            key = "bring_pc_112" if is_pc else "bring_device_112"
            comment = (
                "Ждем вас в АБК 3, каб. 112 с ПК.\nЕсли возникнут вопросы, напишите в комментариях к этой заявке."
                if is_pc else
                "Требуется принести устройство в АБК-3, 112 кабинет для диагностики.\nЕсли возникнут вопросы, напишите в комментариях к этой заявке."
            )
            return RuleDecision(
                template_key=key,
                rule_type="hardware_repair",
                name="Ждем в АБК-3 с ПК" if is_pc else "Принести устройство в каб. 112 (Диагностика/Ремонт)",
                status_id=48,
                status_name="Ожидание устройства",
                expenses=10,
                comment=comment,
            )

        # 2. Аппаратный ремонт, установка комплектующих, сгоревшие компоненты, физические повреждения
        is_hardware_issue = any(w in user_text for w in [
            "диагностика пк", "диагностика компьютера", "новый процессор", "новый системный",
            "замена диска", "замена hdd", "замена ssd", "черный экран", "пищит компьютер",
            "замена памяти", "аппаратный ремонт", "сгорел", "задымился", "второй монитор",
            "видеокарт", "материнск", "блок питания", "кулер", "замена термопасты",
            "разбит экран", "треснул", "уронили", "не загорается экран", "разбит", "поврежден корпус",
        ])
        if is_hardware_issue:
            is_pc = any(w in user_text for w in ["пк", "компьютер", "комп", "системн", "системник", "блок", "ноутбук", "моноблок", "экран"])
            device_name = "ноутбук" if any(w in user_text for w in ["ноутбук", "ноутах", "laptop"]) else "системный блок"
            return RuleDecision(
                template_key="hardware_repair" if is_pc else "bring_device_112",
                rule_type="hardware_repair",
                name="Обслуживание и ремонт ПК в 112 каб." if is_pc else "Принести устройство в каб. 112 (Диагностика/Ремонт)",
                status_id=48,
                status_name="Ожидание устройства",
                expenses=10,
                comment=(
                    f"Приносите {device_name} в АБК 3, 112 каб. на диагностику, обслуживание и настройку. О времени визита вы можете написать в комментариях к этой заявке."
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
            "принтер", "мфу", "сканер", "интернет", "сайт", "браузер", "wifi", "вайфай",
            "приложение", "программа", "программ"
        ]) and not any(w in user_text for w in ["не включается", "сгорел", "задымился", "пищит", "чистка", "пыл", "синий экран", "bsod", "сам компьютер", "весь пк"])

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
                rule_type="hardware_repair",
                name="Обслуживание и ремонт ПК в 112 каб.",
                status_id=48,
                status_name="Ожидание устройства",
                expenses=10,
                comment=f"Приносите {device_name} в АБК 3, 112 каб. на диагностику, обслуживание и настройку. О времени визита вы можете написать в комментариях к этой заявке.",
            )

        # 4. Семантический шлюз (FastEmbed Semantic Anchors) для синонимов, сленга и контекста
        try:
            from .semantic_classifier import classify_semantic_intent
            intent, score = classify_semantic_intent(user_text, threshold=0.75)
            if intent in ("hardware_repair", "bring_device_112"):
                is_notebook = any(w in user_text for w in ["ноутбук", "ноутах", "laptop"])
                device_name = "ноутбук" if is_notebook else "системный блок"
                return RuleDecision(
                    template_key="hardware_repair" if intent == "hardware_repair" else "bring_device_112",
                    rule_type="hardware_repair",
                    name="Обслуживание и ремонт ПК в 112 каб." if intent == "hardware_repair" else "Принести устройство в каб. 112 (Диагностика/Ремонт)",
                    status_id=48,
                    status_name="Ожидание устройства",
                    expenses=10,
                    comment=f"Приносите {device_name} в АБК 3, 112 каб. на диагностику, обслуживание и настройку. О времени визита вы можете написать в комментариях к этой заявке.",
                )
        except Exception:
            pass

        return None
