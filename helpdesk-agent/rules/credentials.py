import re
from typing import Any

from .base import BaseRule, RuleDecision

try:
    from executors.ad import ActiveDirectoryExecutor, generate_sam_account_name
except (ImportError, ValueError):
    from helpdesk_agent.executors.ad import ActiveDirectoryExecutor, generate_sam_account_name


class CredentialsRule(BaseRule):
    """
    Правило: Учетные записи, пароли, доступы (Создание УЗ в AD, Wi-Fi, Почта, сброс пароля AD).
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
        service_name = (task.get("ServiceName") or "").lower()
        service_id = task.get("ServiceId")
        user_text = f"{name} {desc} {service_name}".strip()

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

        # 2. Создание нового пользователя в Active Directory
        is_user_creation = (
            any(w in user_text for w in [
                "создание учетной записи", "создать учетную запись", "создание пользователя",
                "создать пользователя", "новый пользователь", "создание уз", "создать уз",
                "новая учетная запись", "заявка на создание пользователя", "заявка на создание учетной записи"
            ])
            or service_id in (42, 53, 54, 55, 124, 104, 186)
            or (task.get("ServiceParentId") == 42 and service_id != 63)
        )
        if is_user_creation and not any(w in user_text for w in ["почт", "сброс", "заблокирован"]):
            details = ActiveDirectoryExecutor.extract_user_creation_details_from_task(task)
            surname = details.get("surname")
            emp_name = details.get("name")
            patronymic = details.get("patronymic")

            if surname and emp_name:
                sam_preview = generate_sam_account_name(surname, emp_name, patronymic)
                fio_display = f"{surname} {emp_name}" + (f" {patronymic}" if patronymic else "")
                return RuleDecision(
                    template_key="user_created",
                    name=f"⚡ Создание пользователя AD ({sam_preview}) ➔ Выполнена (29)",
                    status_id=29,
                    status_name="Выполнена",
                    expenses=10,
                    comment=(
                        "Учетная запись успешно создана.\n"
                        f"Логин: {sam_preview}\n"
                        "Временный пароль: {password}\n"
                        "При первом входе в систему потребуется изменить пароль на постоянный.\n"
                        "Если возникнут сложности со входом, напишите в комментариях к этой заявке или подходите в АБК-3, кабинет 112."
                    ),
                )
            else:
                return RuleDecision(
                    template_key="account_details_clarify",
                    name="Уточнение реквизитов для создания УЗ",
                    status_id=35,
                    status_name="Требует уточнения",
                    expenses=5,
                    comment=(
                        "Добрый день! Для создания учетной записи сотрудника в Active Directory, пожалуйста, "
                        "укажите ФИО полностью, должность и подразделение ответным комментарием к этой заявке."
                    ),
                )

        # 3. Выдача Wi-Fi (Автоматизируемое действие через Active Directory)
        is_wifi_request = any(w in user_text for w in [
            "wi-fi", "wifi", "вайфай", "вай-фай", "work-net", "пароль от сети", "пароль от wi-fi", "доступ к wi-fi"
        ])
        if is_wifi_request and not any(w in user_text for w in ["excel", "exle", "обменник", "папк", "диск", "1с", "принтер"]):
            return RuleDecision(
                template_key="wifi_access",
                name="⚡ Автовыдача доступа WLAN в AD ➔ Выполнена (29)",
                status_id=27,
                status_name="В работе",
                expenses=10,
                comment=(
                    "Доступ к Wi-Fi предоставлен.\n"
                    "Используйте логин и пароль от вашей учетной записи на ПК. Инструкцию по подключению приложил.\n"
                    "Если возникнут проблемы с подключением, приходите в АБК-3, кабинет 112."
                ),
            )

        # 4. Создание электронной почты (Статус 27 В работе)
        if "почт" in user_text and any(w in user_text for w in ["создать почту", "создание почты", "электронная почта", "новый ящик"]):
            return RuleDecision(
                template_key="in_work_standard",
                name="Создание корпоративной почты (в работе)",
                status_id=27,
                status_name="В работе",
                expenses=10,
                comment="Добрый день! Заявка на создание почтового ящика принята в работу. После создания учетные данные будут направлены в комментариях к этой заявке.",
            )

        return None

