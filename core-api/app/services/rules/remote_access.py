from typing import Any

from shared.domain import ClarificationRequired, DecisionOutcome, Evidence, NoMatch

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
                    "После установки укажите в комментарии к этой заявке ваш идентификатор и пароль от программы."
                ),
            )

        return None

    def evaluate_typed(
        self,
        task: dict[str, Any],
        diag: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        redirect_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> DecisionOutcome:
        dec = self.evaluate(task, diag, kb_matches, redirect_mode, context)
        if dec is not None and dec.template_key == "anydesk_fallback_assistant":
            return ClarificationRequired(
                rule_key="remote_access.assistance",
                rule_version="2",
                outcome_key="anydesk_fallback_assistant",
                missing_fields=["assistant_credentials"],
                evidence=[
                    Evidence(
                        source="rule",
                        field="diag.is_online",
                        code="anydesk_connection_failed",
                        detail="Host is online but AnyDesk connection failed",
                    )
                ],
            )
        return NoMatch(rule_key="remote_access.assistance", rule_version="2")

