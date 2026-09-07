from shared.domain import ActionProposed, ClarificationRequired

from app.services.rules.credentials import CredentialsRule


def _ticket(**raw_fields):
    return {
        "Id": 140479,
        "ServiceId": 53,
        "Name": "Создание учетной записи",
        "Description": "Новый сотрудник",
        "_field_meta": {"raw": raw_fields},
    }


def test_regression_140479_invalid_test_values_require_clarification_and_no_action():
    task = _ticket(**{"1057": "test", "1058": "тест", "1065": "-", "1064": "-"})
    rule = CredentialsRule()

    typed = rule.evaluate_typed(task)
    legacy = rule.evaluate(task)

    assert isinstance(typed, ClarificationRequired)
    assert typed.outcome_key == "account_details_invalid"
    assert legacy is not None
    assert legacy.template_key == "account_details_clarify"
    assert legacy.status_id == 35
    assert "action" not in legacy.to_dict()


def test_valid_create_user_is_proposal_never_completion():
    task = _ticket(
        **{
            "1057": "Иванов",
            "1058": "Иван",
            "1059": "Иванович",
            "1065": "Инженер",
            "1064": "ИТ",
            "1074": "Интра",
        }
    )
    rule = CredentialsRule()

    typed = rule.evaluate_typed(task)
    legacy = rule.evaluate(task)

    assert isinstance(typed, ActionProposed)
    assert typed.action == "create_user"
    assert typed.requires_approval is True
    assert legacy is not None and legacy.status_id != 29
    assert "успешно создан" not in legacy.comment.lower()
