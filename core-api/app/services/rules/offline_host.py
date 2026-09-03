from typing import Any

from .base import BaseRule, RuleDecision


class OfflineHostRule(BaseRule):
    """
    Правило: Проверка физической недоступности рабочей станции (Статус 35 `pc_offline`).
    Исключает заявки на списание, групповой монтаж или мероприятия.
    """

    def __init__(self, priority: int = 70):
        super().__init__(priority=priority)

    @property
    def name(self) -> str:
        return "OfflineHostRule"

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

        # Исключения: мероприятия, списание, физический монтаж
        is_decommission_or_event = any(w in user_text for w in [
            "списание", "списать", "дефектовк", "акт о неисправности", "конференц", "обучение",
            "подключить 6 компьютеров", "подключить компьютеры к сети", "акт экспертизы"
        ])
        if is_decommission_or_event:
            return None

        # Проверка статуса хоста
        if diag and not diag.get("is_online", False):
            target = diag.get("target") or "ПК"
            if target != "UNKNOWN":
                comment = (
                    f"Не вижу ПК {target} в сети.\n"
                    f"1. Убедитесь в корректности имени ПК;\n"
                    f"2. Перезагрузите компьютер;\n"
                    f"3. Проверьте подключение сетевого кабеля;\n"
                    f"4. Если кабель подключен, проверьте наличие световой индикации в месте подключения кабеля.\n"
                    f"Пожалуйста, напишите в комментариях к заявке, когда ПК будет включен и доступен в сети."
                )
                return RuleDecision(
                    template_key="pc_offline",
                    name="Не вижу ПК в сети",
                    status_id=35,
                    status_name="Требует уточнения",
                    expenses=5,
                    comment=comment,
                )

        return None
