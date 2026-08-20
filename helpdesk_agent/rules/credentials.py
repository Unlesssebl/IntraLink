from typing import Any

from .base import BaseRule, RuleDecision


class CredentialsRule(BaseRule):
    """
    Правило: Учетные записи, пароли, доступы (Wi-Fi, Почта, сброс пароля AD).
    """

    def __init__(self, priority: int = 30):
        super().__init__(priority=priority)

    @property
    def name(self) -> str:
        return "CredentialsRule"

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

        # 1. Забытый пароль / блокировка входа в ОС (не слать pc_offline!)
        is_password_issue = any(w in user_text for w in [
            "не помню пароль", "забыла пароль", "забыл пароль", "сбросить пароль", "сброс пароля",
            "проблема со входом в ноутбук", "проблема со входом в компьютер", "не могу войти в ноутбук",
            "не могу войти в компьютер", "заблокирован пароль", "заблокирована учетная запись"
        ])
        if is_password_issue:
            return RuleDecision(
                template_key="password_reset_call",
                name="Сброс пароля учетной записи (звонок на 49-87)",
                status_id=27,
                status_name="В работе",
                expenses=10,
                comment=(
                    "Добрый день! Ваша заявка принята в работу. "
                    "Для сброса пароля учетной записи перезвоните, пожалуйста, с рабочего телефона на номер 49-87 для подтверждения личности."
                ),
            )

        # 2. Выдача Wi-Fi (Статус 29)
        is_wifi_request = any(w in user_text for w in [
            "wi-fi", "wifi", "вайфай", "вай-фай", "work-net", "пароль от сети", "пароль от wi-fi", "доступ к wi-fi"
        ])
        if is_wifi_request and not any(w in user_text for w in ["excel", "exle", "обменник", "папк", "диск", "1с", "принтер"]):
            return RuleDecision(
                template_key="wifi_access",
                name="Предоставление Wi-Fi",
                status_id=29,
                status_name="Выполнена",
                expenses=10,
                comment=(
                    "Доступ к Wi-Fi предоставлен.\n"
                    "Используйте логин и пароль от вашей учетной записи на ПК. Инструкцию по подключению приложил.\n"
                    "Если возникнут проблемы с подключением, приходите в АБК-3, кабинет 112."
                ),
            )

        # 3. Создание электронной почты (Статус 29)
        if "почт" in user_text and any(w in user_text for w in ["создать почту", "создание почты", "электронная почта", "новый ящик"]):
            return RuleDecision(
                template_key="email_created",
                name="Создание почты выполнено",
                status_id=29,
                status_name="Выполнена",
                expenses=10,
                comment="Заявка выполнена, логин и пароль для входа в почту указаны в зеленых рамках выше.\nЕсли возникнут сложности со входом, напишите в комментариях к этой заявке.",
            )

        return None
