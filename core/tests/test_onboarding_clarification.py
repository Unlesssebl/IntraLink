"""Canonical employee-onboarding clarification behavior."""

from core.automation.clarification import (
    clarification_fingerprint,
    extract_labeled_facts,
    latest_human_public_event,
    merge_onboarding_facts,
    render_clarification,
)
from core.automation.workflows import get_default_workflow_registry
from core.intraservice.dto import TaskLifetimeEventDTO


def test_fingerprint_is_stable_for_missing_fact_order() -> None:
    first = clarification_fingerprint(42, "a" * 64, ["title", "department"])
    second = clarification_fingerprint(42, "a" * 64, ["department", "title"])
    assert first == second


def test_fio_template_and_literal_extraction() -> None:
    assert render_clarification(["last_name", "first_name"]).startswith("Укажите, пожалуйста, полные ФИО")
    facts = extract_labeled_facts(
        "Иванов Иван Иванович",
        ["last_name", "first_name", "middle_name"],
    )
    assert facts["last_name"]["value"] == "Иванов"
    assert facts["first_name"]["text_span"] == "Иванов Иван Иванович"


def test_only_requested_labelled_facts_are_extracted() -> None:
    facts = extract_labeled_facts(
        "Подразделение: ИТ\nДолжность: Инженер\nКомпания: Тест",
        ["department", "title"],
    )
    assert {key: item["value"] for key, item in facts.items()} == {
        "department": "ИТ",
        "title": "Инженер",
    }


def test_comment_never_overwrites_structured_fact_and_creates_conflict() -> None:
    merged = merge_onboarding_facts(
        {"department": "ИТ"},
        [{"event_id": 7, "facts": {"department": {"value": "Бухгалтерия", "text_span": "Подразделение: Бухгалтерия"}}}],
    )
    assert merged.values["department"] == "ИТ"
    assert merged.conflicts["department"] == ["ИТ", "Бухгалтерия"]
    assert len(merged.provenance["department"]) == 2


def test_new_human_event_ignores_bot_private_and_status_events() -> None:
    events = [
        TaskLifetimeEventDTO(Id=11, EditorId=99, Comment="Вопрос", IsPrivateComment=False),
        TaskLifetimeEventDTO(Id=12, EditorId=12, Comment="Секрет", IsPrivateComment=True),
        TaskLifetimeEventDTO(Id=13, EditorId=12, StatusId=6),
        TaskLifetimeEventDTO(Id=14, EditorId=12, Comment="Должность: Инженер", IsPrivateComment=False),
    ]
    selected = latest_human_public_event(events, baseline_event_id=11, bot_user_id=99, applicant_id=12)
    assert selected is not None and selected.id == 14


def test_creation_and_revocation_workflows_have_disjoint_capabilities() -> None:
    registry = get_default_workflow_registry()
    onboarding = registry.for_case_type("employee_onboarding")
    revocation = registry.for_case_type("access_revocation_request")
    assert onboarding is not None and onboarding.key == "employee_onboarding_workflow"
    assert onboarding.allowed_capabilities == ("create_ad_user",)
    assert "disable_ad_user" not in onboarding.allowed_capabilities
    assert revocation is not None and revocation.key == "access_revocation_workflow"
    assert revocation.allowed_capabilities == ("disable_ad_user",)
    assert "create_ad_user" not in revocation.allowed_capabilities


def test_latest_human_public_event_ignores_operator_posted_bot_question() -> None:
    question = "Укажите, пожалуйста, полные ФИО сотрудника, для которого необходимо создать учётную запись."
    events = [
        # Event 1: Baseline before clarification
        TaskLifetimeEventDTO(Id=1, EditorId=8664, Comment="Предыдущий комментарий", IsPrivateComment=False),
        # Event 2: Bot question erroneously published with operator's token (EditorId=8664 instead of 10502)
        TaskLifetimeEventDTO(Id=2, EditorId=8664, Comment=question, IsPrivateComment=False),
        # Event 3: Actual human answer
        TaskLifetimeEventDTO(Id=3, EditorId=8664, Comment="Ахмедов Ахмед Ахмедович", IsPrivateComment=False),
    ]
    # With question_text guard, event 2 is rejected even though EditorId != 10502
    selected = latest_human_public_event(
        events,
        baseline_event_id=1,
        bot_user_id=10502,
        applicant_id=8664,
        question_text=question,
    )
    assert selected is not None
    assert selected.id == 3
    assert selected.comment == "Ахмедов Ахмед Ахмедович"


def test_is_public_false_is_treated_as_private() -> None:
    event_dict = {"Id": 5, "Comment": "Внутренняя заметка", "IsPublic": False}
    event = TaskLifetimeEventDTO.model_validate(event_dict)
    assert event.is_private is True

    event_dict_str = {"Id": 6, "Comment": "Внутренняя заметка", "IsPublic": "false"}
    event_str = TaskLifetimeEventDTO.model_validate(event_dict_str)
    assert event_str.is_private is True


def test_latest_human_event_reconciles_legacy_baseline_at_question_boundary() -> None:
    question = "Укажите ФИО"
    events = [
        TaskLifetimeEventDTO(Id=101, EventKey="a" * 64, EditorId=8664, Comment="Старый ответ"),
        TaskLifetimeEventDTO(Id=202, EventKey="b" * 64, EditorId=10502, Comment=question),
        TaskLifetimeEventDTO(
            Id=303,
            EventKey="c" * 64,
            EditorId=8664,
            Comment="Ахмедов Ахмед Ахмедович",
        ),
    ]
    selected = latest_human_public_event(
        events,
        baseline_event_id=2,  # legacy positional value no longer present
        bot_user_id=10502,
        applicant_id=8664,
        question_text=question,
    )
    assert selected is not None
    assert selected.event_key == "c" * 64


def test_latest_human_event_rejects_other_operator_comment() -> None:
    events = [
        TaskLifetimeEventDTO(Id=1, EventKey="a" * 64, EditorId=10502, Comment="Укажите ФИО"),
        TaskLifetimeEventDTO(Id=2, EventKey="b" * 64, EditorId=777, Comment="Иванов Иван Иванович"),
    ]
    assert (
        latest_human_public_event(
            events,
            baseline_event_id=1,
            baseline_event_key="a" * 64,
            bot_user_id=10502,
            applicant_id=8664,
            question_text="Укажите ФИО",
        )
        is None
    )
