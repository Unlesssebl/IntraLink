from typing import Any

from .base import BaseRule, RuleDecision


class PrinterRule(BaseRule):
    """
    Правило: Оргтехника, сетевые МФУ и локальные принтеры.
    """

    def __init__(self, priority: int = 60):
        super().__init__(priority=priority)

    @property
    def name(self) -> str:
        return "PrinterRule"

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

        is_printer_topic = any(w in user_text for w in [
            "принтер", "мфу", "kyocera", "ecosys", "hp laserjet", "canon", "xerox",
            "pantum", "пантум", "этикеточный", "печать", "картридж"
        ])

        if not is_printer_topic:
            return None

        # 1. Если диагностика проверяла МФУ и оно офлайн
        target_name = (diag.get("target") if diag else "") or ""
        is_mfu_target = "P" in target_name.upper() or any(w in target_name.upper() for w in ["MFU", "PRN"])

        if diag and not diag.get("is_online", False) and is_mfu_target:
            return RuleDecision(
                template_key="printer_offline",
                name="Не вижу МФУ в сети",
                status_id=35,
                status_name="Требует уточнения",
                expenses=5,
                comment=(
                    "Не вижу МФУ в сети.\n"
                    "1. Убедитесь в корректности имени/ IP адреса принтера;\n"
                    "2. Перезагрузите МФУ;\n"
                    "3. Переподключите сетевой кабель к МФУ (в случае USB подключения — переподключите USB кабель к МФУ);\n"
                    "Пожалуйста, напишите в комментариях к заявке о результатах проверки."
                ),
            )

        # 2. Если требуется уточнение параметров подключения принтера
        if any(w in user_text for w in ["не печатает", "подключить принтер", "настроить принтер", "ip принтера"]):
            if diag and diag.get("is_online", False):
                # ПК в сети, но параметры принтера не ясны
                return RuleDecision(
                    template_key="printer_ip_clarify",
                    name="Уточнение IP адреса / типа подключения принтера",
                    status_id=35,
                    status_name="Требует уточнения",
                    expenses=5,
                    comment=(
                        "Если принтер сетевой, укажите IP адрес (указан на самом принтере, в формате 10.244.***.***).\n"
                        "В случае подключения по USB укажите номер ПК, к которому подключен принтер.\n"
                        "Прошу дать ответ в комментариях к этой заявке."
                    ),
                )

        return None
