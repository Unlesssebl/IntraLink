from typing import Any

from .base import BaseRule, RuleDecision
from app.utils.ad_utils import (
    generate_sam_account_name,
    extract_person_candidate_from_task,
)
from shared.domain import (
    ActionProposed,
    ClarificationRequired,
    CreateUserParameters,
    DecisionOutcome,
    Evidence,
    ManualReviewRequired,
    NoMatch,
    PersonCandidate,
    validate_person_candidate,
)


class CredentialsRule(BaseRule):
    """
    Правило: Учетные записи, пароли, доступы (Создание УЗ в AD, Wi-Fi, Почта, сброс пароля AD).
    """

    def __init__(self, priority: int = 30):
        super().__init__(priority=priority)

    @property
    def name(self) -> str:
        return "CredentialsRule"

    def evaluate_typed(self, task: dict[str, Any]) -> DecisionOutcome:
        """Evaluate the AD-user branch without producing a completion claim."""
        service_id = task.get("ServiceId")
        service_parent_id = task.get("ServiceParentId")
        user_text = " ".join(
            str(task.get(key) or "").lower()
            for key in ("Name", "Description", "ServiceName")
        ).strip()
        is_user_creation = (
            any(
                marker in user_text
                for marker in (
                    "создание учетной записи",
                    "создать учетную запись",
                    "создание пользователя",
                    "создать пользователя",
                    "новый пользователь",
                    "создание уз",
                    "создать уз",
                    "новая учетная запись",
                    "заявка на создание пользователя",
                    "заявка на создание учетной записи",
                )
            )
            or service_id in (42, 53, 54, 55, 124, 104, 186)
            or (service_parent_id == 42 and service_id != 63)
        )
        if not is_user_creation or any(marker in user_text for marker in ("почт", "сброс", "заблокирован")):
            return NoMatch(
                rule_key="credentials.user_creation",
                rule_version="2",
            )

        if task.get("_fact_extraction_manual_review"):
            return ManualReviewRequired(
                rule_key="credentials.user_creation",
                rule_version="2",
                reason=str(task["_fact_extraction_manual_review"]),
            )
        candidate = (
            PersonCandidate.model_validate(task["_extracted_person"])
            if task.get("_extracted_person")
            else extract_person_candidate_from_task(task)
        )
        validation = validate_person_candidate(candidate)
        if not validation.valid:
            invalid_fields = sorted({error.field for error in validation.errors})
            return ClarificationRequired(
                rule_key="credentials.user_creation",
                rule_version="2",
                outcome_key="account_details_invalid",
                invalid_fields=invalid_fields,
                context={"invalid_fields": ", ".join(invalid_fields)},
                evidence=[
                    Evidence(
                        source="rule",
                        field=error.field,
                        code=error.code,
                        detail=error.message,
                    )
                    for error in validation.errors
                ],
            )

        person = validation.person
        assert person is not None
        missing_business_fields = [
            field for field in ("company", "department", "title")
            if not getattr(person, field).strip()
        ]
        if missing_business_fields:
            return ClarificationRequired(
                rule_key="credentials.user_creation",
                rule_version="2",
                outcome_key="account_details_invalid",
                missing_fields=missing_business_fields,
                context={"invalid_fields": ", ".join(missing_business_fields)},
                evidence=[
                    Evidence(source="rule", field=field, code="missing")
                    for field in missing_business_fields
                ],
            )
        return ActionProposed(
            rule_key="credentials.user_creation",
            rule_version="2",
            outcome_key="create_user_proposed",
            action="create_user",
            parameters=CreateUserParameters(**person.model_dump()),
            risk_level=2,
            requires_approval=True,
            evidence=[
                Evidence(source="structured", field="surname", code="validated"),
                Evidence(source="structured", field="name", code="validated"),
            ],
        )

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
                rule_type="credentials_reset",
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
            typed_outcome = self.evaluate_typed(task)
            if isinstance(typed_outcome, ActionProposed):
                params = typed_outcome.parameters
                assert isinstance(params, CreateUserParameters)
                sam_preview = generate_sam_account_name(params.surname, params.name, params.patronymic)
                return RuleDecision(
                    template_key="create_user_proposed",
                    rule_type="user_creation",
                    name=f"Создание пользователя AD ({sam_preview}) — требуется подтверждение",
                    status_id=27,
                    status_name="В работе",
                    expenses=10,
                    comment=(
                        "Реквизиты сотрудника прошли проверку. Создание учетной записи "
                        "требует подтверждения оператора и успешной верификации в Active Directory."
                    ),
                    extra={
                        "typed_outcome": typed_outcome.model_dump(mode="json"),
                        "action": "create_user",
                        "action_parameters": params.model_dump(mode="json"),
                    },
                )
            if isinstance(typed_outcome, ClarificationRequired):
                return RuleDecision(
                    template_key="account_details_clarify",
                    rule_type="user_creation",
                    name="Уточнение реквизитов для создания УЗ",
                    status_id=35,
                    status_name="Требует уточнения",
                    expenses=5,
                    comment=(
                        "Добрый день! Для создания учетной записи сотрудника в Active Directory, пожалуйста, "
                        "укажите ФИО полностью, должность и подразделение ответным комментарием к этой заявке."
                    ),
                    extra={"typed_outcome": typed_outcome.model_dump(mode="json")},
                )
            if isinstance(typed_outcome, ManualReviewRequired):
                return RuleDecision(
                    template_key="account_details_clarify",
                    rule_type="user_creation",
                    name="Ручная проверка реквизитов создания УЗ",
                    status_id=35,
                    status_name="Требует уточнения",
                    expenses=5,
                    comment=(
                        "Добрый день! Не удалось однозначно извлечь реквизиты сотрудника. "
                        "Укажите, пожалуйста, ФИО полностью, должность, организацию и подразделение."
                    ),
                    extra={"typed_outcome": typed_outcome.model_dump(mode="json")},
                )

        # 3. Выдача Wi-Fi (Автоматизируемое действие через Active Directory)
        is_wifi_request = any(w in user_text for w in [
            "wi-fi", "wifi", "вайфай", "вай-фай", "work-net", "пароль от сети", "пароль от wi-fi", "доступ к wi-fi"
        ])
        if is_wifi_request and not any(w in user_text for w in ["excel", "exle", "обменник", "папк", "диск", "1с", "принтер"]):
            return RuleDecision(
                template_key="wifi_access",
                rule_type="wlan_access",
                name="⚡ Автовыдача доступа WLAN в AD ➔ Выполнена (29)",
                status_id=29,
                status_name="Выполнена",
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

