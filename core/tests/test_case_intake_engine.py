import json

import pytest

from core.automation.case_router import CaseRouter
from core.automation.contracts import CaseDecisionState, ExtractionMethod, TicketSnapshot
from core.automation.frame_extractor import CaseFrameExtractor
from core.automation.intake import CaseIntakeEngine
from core.automation.llm_transport import LLMTransportTimeoutError
from core.automation.service_routing import (
    RedirectResolver,
    ServiceCatalogEntry,
    TargetSelectionState,
    TargetServiceResolution,
)
from core.automation.snapshot import compute_canonical_snapshot_hash


def _snapshot(
    *,
    title: str,
    description: str,
    service_id: int | None = None,
    service_name: str | None = None,
    entities: dict[str, str] | None = None,
) -> TicketSnapshot:
    payload = {
        "task_id": 101,
        "status_id": 1,
        "service_id": service_id,
        "service_name": service_name,
        "title": title,
        "description": description,
        "public_comments": [],
        "custom_fields": {},
        "entities": entities or {},
        "attachments": [],
        "last_event_id": None,
    }
    return TicketSnapshot(**payload, snapshot_hash=compute_canonical_snapshot_hash(payload))


class FakeTransport:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.payload = None
        self.kwargs = None

    async def complete_json(self, **kwargs) -> str:
        self.kwargs = kwargs
        self.payload = kwargs["payload"]
        return json.dumps(self.response, ensure_ascii=False)


class FailingTransport:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def complete_json(self, **kwargs) -> str:
        self.calls += 1
        raise self.error


class SupportingVerifier:
    prompt_version = "test-verifier-v1"

    async def verify(self, snapshot, frame, candidates):
        return ({candidate.case_type: "supported" for candidate in candidates}, {"test": True})


@pytest.mark.asyncio
async def test_case_frame_extractor_grounds_llm_assertions_and_removes_secret_entities() -> None:
    transport = FakeTransport(
        {
            "assertions": [
                {
                    "kind": "intent",
                    "key": "connect_printer",
                    "value": "true",
                    "source_ref": "description",
                    "text_span": "подключить принтер",
                    "is_negated": False,
                }
            ]
        }
    )
    snapshot = _snapshot(
        title="Принтер",
        description="Прошу подключить принтер. Пароль: qwerty123",
        entities={"pc_name": "WKS-01", "it_password": "never-store"},
    )

    frame = await CaseFrameExtractor(transport=transport).extract(snapshot)

    assert frame.entities == {"pc_name": "WKS-01"}
    assert any(item.extraction_method == ExtractionMethod.llm for item in frame.assertions)
    assert transport.kwargs["timeout_seconds"] == 30.0
    assert "connect_printer" in transport.payload["allowed_keys"]
    serialized_payload = json.dumps(transport.payload, ensure_ascii=False)
    assert "qwerty123" not in serialized_payload
    assert "never-store" not in frame.model_dump_json()


@pytest.mark.asyncio
async def test_invalid_llm_span_degrades_frame_without_losing_deterministic_facts() -> None:
    transport = FakeTransport(
        {
            "assertions": [
                {
                    "kind": "intent",
                    "key": "create_user",
                    "value": "true",
                    "source_ref": "description",
                    "text_span": "текста здесь нет",
                }
            ]
        }
    )
    snapshot = _snapshot(
        title="Пользователь",
        description="Создать пользователя",
        entities={"first_name": "Иван"},
    )

    frame = await CaseFrameExtractor(transport=transport).extract(snapshot)

    assert frame.entities["first_name"] == "Иван"
    assert frame.degraded_components == {"case_frame_extractor": "extractor_invalid_response"}


@pytest.mark.asyncio
async def test_extractor_records_typed_timeout_reason() -> None:
    transport = FailingTransport(LLMTransportTimeoutError("late"))
    snapshot = _snapshot(title="Неизвестно", description="Нужна помощь")

    frame = await CaseFrameExtractor(transport=transport).extract(snapshot)

    assert frame.degraded_components == {"case_frame_extractor": "extractor_timeout"}


@pytest.mark.asyncio
async def test_intake_skips_llm_when_deterministic_evidence_is_decisive() -> None:
    transport = FailingTransport(AssertionError("LLM must not be called"))
    snapshot = _snapshot(
        title="Установка принтера",
        description="Прошу подключить принтер",
        service_id=183,
    )
    engine = CaseIntakeEngine(CaseFrameExtractor(transport=transport), CaseRouter())

    frame, decision = await engine.analyze(snapshot)

    assert transport.calls == 0
    assert frame.degraded_components == {}
    assert decision.state == CaseDecisionState.selected
    assert decision.primary_case_type == "printer_connection_request"


@pytest.mark.asyncio
async def test_target_service_drives_case_selection_before_llm() -> None:
    transport = FailingTransport(AssertionError("LLM must not be called"))
    snapshot = _snapshot(
        title="Установить приложение",
        description="Требуется стандартное рабочее ПО",
        service_id=59,
    )
    catalog_hash = "a" * 64
    target = RedirectResolver().resolve_target(
        snapshot=snapshot,
        catalog_hash=catalog_hash,
        entries=[
            ServiceCatalogEntry(
                service_id=59,
                service_path="02. Установка и настройка программ → Установка, настройка (ПО)",
                is_active=True,
                catalog_hash=catalog_hash,
            )
        ],
    )
    engine = CaseIntakeEngine(CaseFrameExtractor(transport=transport), CaseRouter())

    frame, decision = await engine.analyze(snapshot, target_resolution=target)

    assert transport.calls == 0
    assert frame.degraded_components == {}
    assert decision.state == CaseDecisionState.selected
    assert decision.primary_case_type == "software_installation_request"
    assert any(item.source == "target_service_id" for item in decision.evidence)


@pytest.mark.asyncio
async def test_hardware_diagnostic_is_selected_from_text_within_service_scope() -> None:
    transport = FailingTransport(AssertionError("LLM must not be called"))
    snapshot = _snapshot(
        title="Акт дефектовки HDD",
        description="Нужно провести дефектовку HDD на новом компьютере",
        service_id=32,
    )
    target = TargetServiceResolution(
        task_id=snapshot.task_id,
        snapshot_hash=snapshot.snapshot_hash,
        source_service_id=32,
        source_service_path="03. Установка и обслуживание оргтехники → Компьютеры и ноутбуки",
        catalog_hash="c" * 64,
        catalog_state="available",
        state=TargetSelectionState.source_match,
        selected_service_id=32,
        selected_service_path="03. Установка и обслуживание оргтехники → Компьютеры и ноутбуки",
    )

    frame, decision = await CaseIntakeEngine(
        CaseFrameExtractor(transport=transport),
        CaseRouter(),
    ).analyze(snapshot, target_resolution=target)

    assert transport.calls == 0
    assert frame.llm_attempted is False
    assert decision.state == CaseDecisionState.selected
    assert decision.primary_case_type == "workstation_hardware_diagnostic"
    assert any(item.text_span == "Акт дефектовки" for item in decision.evidence)


@pytest.mark.asyncio
async def test_hardware_diagnostic_text_does_not_escape_service_scope() -> None:
    snapshot = _snapshot(
        title="Акт дефектовки HDD",
        description="Нужно провести дефектовку HDD",
        service_id=71,
    )
    target = TargetServiceResolution(
        task_id=snapshot.task_id,
        snapshot_hash=snapshot.snapshot_hash,
        source_service_id=71,
        source_service_path="04. Проблемы с сетью и интернетом → Общие вопросы",
        catalog_hash="c" * 64,
        catalog_state="available",
        state=TargetSelectionState.source_match,
        selected_service_id=71,
        selected_service_path="04. Проблемы с сетью и интернетом → Общие вопросы",
    )

    _, decision = await CaseIntakeEngine(CaseFrameExtractor(), CaseRouter()).analyze(
        snapshot,
        target_resolution=target,
    )

    assert decision.primary_case_type != "workstation_hardware_diagnostic"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_id", "service_path", "expected_case"),
    [
        (53, "01. Учетные записи пользователей → Создание нового пользователя сети", "employee_onboarding"),
        (104, "01. Учетные записи пользователей → Блокировка пользователя", "access_revocation_request"),
        (181, "04. Проблемы с сетью и интернетом → предоставление доступа к Wi-Fi сети", "wireless_access_request"),
        (183, "03. Оргтехника → МФУ и принтеры → Настройка/установка", "printer_connection_request"),
        (184, "03. Оргтехника → МФУ и принтеры → Ремонт", "printing_incident"),
    ],
)
async def test_live_catalog_service_ids_are_deterministic(
    service_id: int,
    service_path: str,
    expected_case: str,
) -> None:
    transport = FailingTransport(AssertionError("LLM must not be called"))
    snapshot = _snapshot(title="Заявка", description="", service_id=service_id)
    catalog_hash = "b" * 64
    target = RedirectResolver().resolve_target(
        snapshot=snapshot,
        catalog_hash=catalog_hash,
        entries=[
            ServiceCatalogEntry(
                service_id=service_id,
                service_path=service_path,
                is_active=True,
                catalog_hash=catalog_hash,
            )
        ],
    )

    _, decision = await CaseIntakeEngine(
        CaseFrameExtractor(transport=transport),
        CaseRouter(),
    ).analyze(snapshot, target_resolution=target)

    assert transport.calls == 0
    assert decision.state == CaseDecisionState.selected
    assert decision.primary_case_type == expected_case


@pytest.mark.asyncio
async def test_grounded_llm_assertion_becomes_case_evidence_in_grey_zone() -> None:
    transport = FakeTransport(
        {
            "assertions": [
                {
                    "kind": "intent",
                    "key": "connect_printer",
                    "value": "true",
                    "source_ref": "description",
                    "text_span": "Подключите устройство",
                    "is_negated": False,
                }
            ]
        }
    )
    snapshot = _snapshot(
        title="Оборудование",
        description="Подключите устройство на рабочем месте",
    )
    engine = CaseIntakeEngine(
        CaseFrameExtractor(transport=transport),
        CaseRouter(verifier=SupportingVerifier()),
    )

    frame, decision = await engine.analyze(snapshot)

    assert any(item.key == "connect_printer" for item in frame.assertions)
    assert decision.state == CaseDecisionState.selected
    assert decision.primary_case_type == "printer_connection_request"
    assert any(item.source == "case_assertion" for item in decision.evidence)


@pytest.mark.asyncio
async def test_router_selects_printer_connection_not_technical_action() -> None:
    snapshot = _snapshot(
        title="Установка принтера",
        description="Прошу подключить принтер к рабочему компьютеру",
        service_id=183,
        service_name="Настройка/установка принтера",
    )
    frame = await CaseFrameExtractor().extract(snapshot)
    decision = await CaseRouter().decide(snapshot, frame)

    assert decision.state == CaseDecisionState.selected
    assert decision.primary_case_type == "printer_connection_request"
    dumped = decision.model_dump_json()
    assert "restart_spooler" not in dumped
    assert "install_printer" not in dumped


@pytest.mark.asyncio
async def test_router_selects_printing_incident_without_diagnosing_spooler() -> None:
    snapshot = _snapshot(
        title="Проблема с печатью",
        description="Принтер не печатает, документ остался в очереди печати",
        service_id=184,
    )
    frame = await CaseFrameExtractor().extract(snapshot)
    decision = await CaseRouter().decide(snapshot, frame)

    assert decision.state == CaseDecisionState.selected
    assert decision.primary_case_type == "printing_incident"


@pytest.mark.asyncio
async def test_router_preserves_two_explicit_intents() -> None:
    snapshot = _snapshot(
        title="Принтер",
        description="Прошу подключить принтер, но старый принтер не печатает",
    )
    frame = await CaseFrameExtractor().extract(snapshot)
    decision = await CaseRouter().decide(snapshot, frame)

    assert decision.state == CaseDecisionState.multi_intent
    assert {decision.primary_case_type, *decision.secondary_case_types} == {
        "printer_connection_request",
        "printing_incident",
    }


@pytest.mark.asyncio
async def test_unknown_case_is_not_forced_into_consultation() -> None:
    snapshot = _snapshot(title="Неизвестное обращение", description="Абстрактный текст без признаков")
    frame = await CaseFrameExtractor().extract(snapshot)
    decision = await CaseRouter().decide(snapshot, frame)

    assert decision.state == CaseDecisionState.unknown
    assert decision.primary_case_type is None
