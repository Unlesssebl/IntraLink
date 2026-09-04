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

        # Извлекаем текст из истории комментариев, если она передана в context
        comments_history = (context or {}).get("comments_history") or []
        comments_text = " ".join(
            (c.get("Comments") or c.get("Comment") or c.get("Text") or "")
            for c in comments_history
        ).lower()
        full_context_text = f"{user_text} {comments_text}".strip()

        # Исключения: мероприятия, списание, физический монтаж, аппаратные поломки ПК и доставка в 112 каб.
        is_app_issue = any(w in full_context_text for w in [
            "приложение", "программ", "1с", "1c", "упп", "erp", "зуп", "directum", "директум",
            "outlook", "браузер", "сайт", "почта", "база"
        ])
        is_hardware_pc_issue = any(w in full_context_text for w in [
            "не включается", "не стартует", "черный экран", "сгорел", "задымился", "пищит",
            "синий экран", "bsod", "аппаратный ремонт", "ремонт компов", "замена диска", "замена ssd",
            "не запускается пк", "не запускается компьютер", "не запускается винда", "не запускается windows"
        ]) or (
            "не запускается" in full_context_text and not is_app_issue
        )
        is_delivery = any(w in full_context_text for w in [
            "112 каб", "112 комн", "каб. 112", "кабинет 112", "кабинете 112", "в 112",
            "принес", "принесла", "принесли", "принесу"
        ])
        is_decommission = any(w in full_context_text for w in [
            "списание", "списать", "дефектовк", "акт о неисправности", "конференц", "обучение",
            "подключить 6 компьютеров", "подключить компьютеры к сети", "акт экспертизы"
        ])
        if is_decommission or is_hardware_pc_issue or is_delivery:
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
