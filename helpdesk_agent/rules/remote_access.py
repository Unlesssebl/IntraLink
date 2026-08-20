from typing import Any

from .base import BaseRule, RuleDecision


class RemoteAccessRule(BaseRule):
    """
    Правило: Проблемы с утилитами удаленного доступа (AnyDesk / Ассистент).
    Учитывает сетевой статус хоста и предотвращает предложение «установить Ассистент»,
    когда хост выключен или обе утилиты недоступны.
    """

    def __init__(self, priority: int = 50):
        super().__init__(priority=priority)

    @property
    def name(self) -> str:
        return "RemoteAccessRule"

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

        is_anydesk_mention = any(w in user_text for w in [
            "anydesk", "энидеск", "анидеск", "any desk"
        ])
        is_assistant_mention = any(w in user_text for w in [
            "ассистент", "мой ассистент", "мойассистент"
        ])

        # Если упоминаются оба инструмента или хост физически офлайн
        host_offline = diag and not diag.get("is_online", False)

        if is_anydesk_mention and is_assistant_mention:
            # Пользователь уже пробовал и Ассистент, и AnyDesk, либо ПК офлайн
            return None  # Передаем дальше в OfflineHostRule / StandardInWorkRule

        if is_anydesk_mention and any(w in user_text for w in ["не подключается", "нет соединения", "ошибка", "сбой"]):
            if host_offline:
                # Хост выключен/нет сети — Ассистент также не заработает
                return None  # Пусть сработает OfflineHostRule

            # Хост в сети, но AnyDesk не соединяется — предлагаем Ассистент
            return RuleDecision(
                template_key="anydesk_fallback_assistant",
                name="AnyDesk не подключается (установка Ассистент)",
                status_id=35,
                status_name="Требует уточнения",
                expenses=5,
                comment=(
                    "Связь через AnyDesk не устанавливается. "
                    "Установите программу «Ассистент» по ссылке: https://мойассистент.рф/скачать/\n"
                    "После установки укажите в комментарии ваш идентификатор и пароль от программы.\n"
                    "По вопросам: 49-87."
                ),
            )

        return None
