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
    selected = latest_human_public_event(events, baseline_event_id=10, bot_user_id=99)
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
