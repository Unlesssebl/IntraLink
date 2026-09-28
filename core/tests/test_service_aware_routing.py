from core.automation.capabilities import get_default_capability_registry
from core.automation.compiler import WorkflowCompiler
from core.automation.contracts import CaseCandidate, CaseDecision, CaseDecisionState, CaseFrame, TicketSnapshot
from core.automation.service_routing import (
    RedirectResolver,
    ServiceCatalogEntry,
    ServiceCompatibilityState,
    ServiceRouteBinding,
    build_redirect_plan,
    canonical_catalog_hash,
    compute_redirect_plan_hash,
)
from core.automation.snapshot import compute_canonical_snapshot_hash
from core.automation.workflows import get_default_workflow_registry
from core.database.models import sanitize_secrets

HASH = "a" * 64


def _decision(case_type: str = "employee_onboarding") -> tuple[CaseFrame, CaseDecision]:
    frame = CaseFrame(
        task_id=1,
        snapshot_hash="1" * 64,
        frame_version="test",
        entities={"first_name": "Иван", "last_name": "Петров", "department": "ИТ", "title": "Инженер"},
    )
    decision = CaseDecision(
        task_id=1,
        snapshot_hash=frame.snapshot_hash,
        frame_id=frame.id,
        router_version="test",
        state=CaseDecisionState.selected,
        primary_case_type=case_type,
        candidates=[CaseCandidate(case_type=case_type, case_type_version="1")],
    )
    return frame, decision


def _binding(service_ids=(900001,), *, active=True) -> ServiceRouteBinding:
    return ServiceRouteBinding(
        key="ad_account_creation",
        version="1",
        service_ids=service_ids,
        allowed_case_types=("employee_onboarding",),
        default_case_type="employee_onboarding",
        allowed_workflows=("employee_onboarding_workflow",),
        allowed_capabilities=("create_ad_user",),
        required_task_type_id=1001,
        required_fields=(),
        redirect_strategy="cancel_and_recreate",
        risk="high",
        is_active=active,
        is_validated=active,
        catalog_hash=HASH,
    )


def _entry(service_id: int, path: str = "01 → AD") -> ServiceCatalogEntry:
    return ServiceCatalogEntry(
        service_id=service_id,
        service_path=path,
        task_type_id=1001,
        is_active=True,
        catalog_hash=HASH,
    )


def _snapshot(service_id: int | None, title: str = "", description: str = "") -> TicketSnapshot:
    payload = {
        "task_id": 1,
        "status_id": 1,
        "service_id": service_id,
        "service_name": None,
        "title": title,
        "description": description,
        "public_comments": [],
        "custom_fields": {},
        "entities": {},
        "attachments": [],
        "last_event_id": None,
    }
    return TicketSnapshot(**payload, snapshot_hash=compute_canonical_snapshot_hash(payload))


def test_catalog_hash_is_stable_and_changes_with_entry() -> None:
    first = _entry(2, "B")
    second = _entry(1, "A")
    assert canonical_catalog_hash([first, second]) == canonical_catalog_hash([second, first])
    assert canonical_catalog_hash([first, second]) != canonical_catalog_hash(
        [first, second.model_copy(update={"is_active": False})]
    )


def test_directum_source_resolves_only_catalog_backed_ad_target() -> None:
    _, decision = _decision()
    directum = ServiceCatalogEntry(
        service_id=55, service_path="05 → Directum", task_type_id=1018, is_active=True, catalog_hash=HASH
    )
    result = RedirectResolver().resolve(
        decision=decision,
        source_service_id=55,
        source_task_type_id=1018,
        catalog_hash=HASH,
        entries=[directum, _entry(900001)],
        bindings=[_binding()],
    )
    assert result.state == ServiceCompatibilityState.mismatch
    assert [candidate.service_id for candidate in result.candidates] == [900001]
    plan = build_redirect_plan(result, decision)
    assert plan.target_service_id == 900001
    assert compute_redirect_plan_hash(plan) == plan.plan_hash


def test_create_ad_user_is_impossible_without_validated_binding() -> None:
    frame, decision = _decision()
    compatibility = RedirectResolver().resolve(
        decision=decision,
        source_service_id=900001,
        source_task_type_id=1001,
        catalog_hash=HASH,
        entries=[_entry(900001)],
        bindings=[_binding(active=False)],
    )
    workflow, action = WorkflowCompiler(get_default_workflow_registry(), get_default_capability_registry()).compile(
        frame=frame, decision=decision, compatibility=compatibility, binding=None
    )
    assert action is None
    assert workflow.disposition.value == "manual"
    assert "create_ad_user" not in [step.key for step in workflow.steps]


def test_known_case_without_workflow_stays_useful_and_manual() -> None:
    frame, decision = _decision("software_installation_request")

    workflow, action = WorkflowCompiler(
        get_default_workflow_registry(),
        get_default_capability_registry(),
    ).compile(frame=frame, decision=decision)

    assert workflow.state.value == "unsupported"
    assert workflow.workflow_key == "unsupported_workflow"
    assert workflow.reason_codes == ["no_registered_workflow"]
    assert workflow.disposition.value == "manual"
    assert action is None


def test_target_service_is_visible_even_when_binding_is_unavailable() -> None:
    _, decision = _decision("printer_connection_request")
    printer = ServiceCatalogEntry(
        service_id=183,
        service_path="03. Оргтехника → МФУ и принтеры → Настройка/установка",
        is_active=True,
        catalog_hash=HASH,
    )

    result = RedirectResolver().resolve(
        decision=decision,
        source_service_id=183,
        source_task_type_id=None,
        catalog_hash=HASH,
        entries=[printer],
        bindings=[],
    )

    assert result.state == ServiceCompatibilityState.degraded
    assert result.authorization_state == "binding_unavailable"
    assert result.target_selection_state.value == "source_match"
    assert result.target_service_id == 183
    assert result.target_service_path == printer.service_path
    assert "case_profile_exact_service_id" in result.target_candidates[0].evidence


def test_target_service_suggestion_is_separate_from_authorization() -> None:
    _, decision = _decision("wireless_access_request")
    source = _entry(55, "05 → Directum")
    wlan = ServiceCatalogEntry(
        service_id=63,
        service_path="01. Доступ → Корпоративный WLAN",
        is_active=True,
        catalog_hash=HASH,
    )

    result = RedirectResolver().resolve(
        decision=decision,
        source_service_id=source.service_id,
        source_task_type_id=None,
        catalog_hash=HASH,
        entries=[source, wlan],
        bindings=[],
    )

    assert result.authorization_state == "binding_unavailable"
    assert result.target_selection_state.value == "target_suggested"
    assert result.target_service_id == 63
    assert result.source_service_id == 55


def test_unknown_source_never_becomes_redirect() -> None:
    _, decision = _decision()
    result = RedirectResolver().resolve(
        decision=decision,
        source_service_id=999999,
        source_task_type_id=1001,
        catalog_hash=HASH,
        entries=[_entry(900001)],
        bindings=[_binding()],
    )
    assert result.state == ServiceCompatibilityState.unknown
    assert result.candidates == []


def test_catalog_first_resolution_selects_active_leaf_without_case_type() -> None:
    software = ServiceCatalogEntry(
        service_id=59,
        service_path="02. Установка и настройка программ → Установка, настройка (ПО)",
        parent_service_id=18,
        is_active=True,
        catalog_hash=HASH,
    )
    parent = ServiceCatalogEntry(
        service_id=18,
        service_path="02. Установка и настройка программ",
        is_active=True,
        catalog_hash=HASH,
    )
    unrelated = ServiceCatalogEntry(
        service_id=104,
        service_path="01. Учетные записи пользователей → Блокировка пользователя",
        is_active=True,
        catalog_hash=HASH,
    )

    resolution = RedirectResolver().resolve_target(
        snapshot=_snapshot(59, "Установить программу"),
        catalog_hash=HASH,
        entries=[parent, software, unrelated],
    )

    assert resolution.state.value == "source_match"
    assert resolution.selected_service_id == 59
    assert resolution.selected_service_path == software.service_path
    assert [candidate.service_id for candidate in resolution.candidates] == [59]
    assert "active_catalog_leaf" in resolution.evidence


def test_catalog_first_resolution_uses_ticket_text_for_parent_service() -> None:
    parent = ServiceCatalogEntry(
        service_id=33,
        service_path="03. Оргтехника → МФУ и принтеры",
        is_active=True,
        catalog_hash=HASH,
    )
    setup = ServiceCatalogEntry(
        service_id=183,
        service_path="03. Оргтехника → МФУ и принтеры → Настройка принтера",
        parent_service_id=33,
        is_active=True,
        catalog_hash=HASH,
    )
    repair = ServiceCatalogEntry(
        service_id=184,
        service_path="03. Оргтехника → МФУ и принтеры → Ремонт принтера",
        parent_service_id=33,
        is_active=True,
        catalog_hash=HASH,
    )

    resolution = RedirectResolver().resolve_target(
        snapshot=_snapshot(33, "Требуется ремонт принтера", "Принтер перестал печатать"),
        catalog_hash=HASH,
        entries=[parent, setup, repair],
    )

    assert resolution.state.value == "target_suggested"
    assert resolution.selected_service_id == 184


def test_routing_evidence_does_not_collide_with_secret_sanitizer() -> None:
    onboarding = ServiceCatalogEntry(
        service_id=53,
        service_path="01. Учетные записи пользователей → Создание нового пользователя сети",
        is_active=True,
        catalog_hash=HASH,
    )
    resolution = RedirectResolver().resolve_target(
        snapshot=_snapshot(53, "Создание нового пользователя сети"),
        catalog_hash=HASH,
        entries=[onboarding],
    )
    payload = resolution.model_dump(mode="json")

    assert sanitize_secrets(payload) == payload
