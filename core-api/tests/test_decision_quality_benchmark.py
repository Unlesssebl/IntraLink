"""Offline decision and reliability benchmark covering all 12 core reliability archetypes."""

import pytest
from unittest.mock import AsyncMock

from sdk.models import HandlerContext
from executors.ad import ADUserCreationResult, ADUserProfile, ADUserStatus
from handlers.create_user import CreateUserHandler
from handlers.grant_wlan import GrantWlanHandler
from shared.domain import (
    Evidence,
    ExtractedTicketFacts,
    FactObservation,
    FactSource,
    FactState,
)
from shared.normalizer import parse_pc_name, parse_printer_address
from app.services.facts.merger import merge_observations
from app.services.fact_extractor import validate_extracted_grounding
from app.services.scenarios import ScenarioContext
from app.services.scenarios.builtin import _printer_install_match


def _obs(key: str, value: str, source: FactSource, ref: str, state: FactState = FactState.VALID, metadata: dict | None = None) -> FactObservation:
    return FactObservation(
        key=key,
        value=value,
        state=state,
        source=source,
        source_ref=ref,
        evidence_span=value if source in {FactSource.COMMENT, FactSource.PARSER, FactSource.LLM} else None,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Archetype 1: User creation with exact FIO matching & read-after-write
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_archetype_1_create_user_success_verified():
    executor = AsyncMock()
    executor.preflight_user_creation_async.return_value = {
        "success": True,
        "dc": "dc1",
        "company_ou": "OU=Интра",
        "department_ou": "OU=ИТ,OU=Интра",
        "required_group": "HLP_Интра",
    }
    executor.search_user_profiles_async.return_value = [
        ADUserProfile(found=False, error="не найден")
    ]
    executor.create_user_account_async.return_value = ADUserCreationResult(
        success=True,
        sam_account_name="ivanov.i.i",
        display_name="Иванов Иван Иванович",
        password="TempPassword-123!",
        user_principal_name="ivanov.i.i@corp.loc",
        distinguished_name="CN=Иванов Иван Иванович,OU=ИТ,OU=Интра",
        groups=["HLP_Интра"],
    )
    executor.get_user_status_async.return_value = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="ivanov.i.i",
        user_principal_name="ivanov.i.i@corp.loc",
        distinguished_name="CN=Иванов Иван Иванович,OU=ИТ,OU=Интра",
        groups=["HLP_Интра"],
    )
    handler = CreateUserHandler(executor)
    ctx = HandlerContext(command_id="arch-1")
    params = {
        "surname": "Иванов",
        "name": "Иван",
        "patronymic": "Иванович",
        "company": "Интра",
        "department": "ИТ",
        "title": "Инженер",
    }
    result = await handler.run_pipeline(ctx, params)
    assert result.success is True
    assert result.payload["verified"] is True
    assert ctx.state["temporary_password"] == "TempPassword-123!"


# ---------------------------------------------------------------------------
# Archetype 2: User creation stopped on ambiguous match
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_archetype_2_create_user_ambiguous_stopped():
    executor = AsyncMock()
    executor.preflight_user_creation_async.return_value = {
        "success": True,
        "dc": "dc1",
        "company_ou": "OU=Интра",
        "department_ou": "OU=ИТ,OU=Интра",
        "required_group": "HLP_Интра",
    }
    executor.search_user_profiles_async.return_value = [
        ADUserProfile(found=True, sam_account_name="ivanov.i.1"),
        ADUserProfile(found=True, sam_account_name="ivanov.i.2"),
    ]
    handler = CreateUserHandler(executor)
    ctx = HandlerContext(command_id="arch-2")
    params = {
        "surname": "Иванов",
        "name": "Иван",
        "patronymic": "Иванович",
        "company": "Интра",
        "department": "ИТ",
        "title": "Инженер",
    }
    result = await handler.run_pipeline(ctx, params)
    assert result.success is False
    assert result.failure_code == "user_ambiguous"
    executor.create_user_account_async.assert_not_awaited()


# ---------------------------------------------------------------------------
# Archetype 3: User creation stopped on disabled existing account
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_archetype_3_create_user_disabled_existing_stopped():
    executor = AsyncMock()
    executor.preflight_user_creation_async.return_value = {
        "success": True,
        "dc": "dc1",
        "company_ou": "OU=Интра",
        "department_ou": "OU=ИТ,OU=Интра",
        "required_group": "HLP_Интра",
    }
    executor.search_user_profiles_async.return_value = [
        ADUserProfile(found=True, sam_account_name="ivanov.i.i", enabled=False)
    ]
    handler = CreateUserHandler(executor)
    ctx = HandlerContext(command_id="arch-3")
    params = {
        "surname": "Иванов",
        "name": "Иван",
        "patronymic": "Иванович",
        "company": "Интра",
        "department": "ИТ",
        "title": "Инженер",
    }
    result = await handler.run_pipeline(ctx, params)
    assert result.success is False
    assert result.failure_code == "user_already_exists"
    assert result.payload["evidence"]["enabled"] is False


# ---------------------------------------------------------------------------
# Archetype 4: WLAN access granted with exact DN group verification
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_archetype_4_grant_wlan_exact_verification():
    executor = AsyncMock()
    executor.get_user_status_async.return_value = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="petrov.p.p",
        groups=["Domain Users"],
        member_of_dns=["CN=Domain Users,CN=Users,DC=corp,DC=loc"],
    )
    executor.grant_wlan_access_async.return_value = {
        "success": True,
        "group": "WLAN-WORKNET",
        "target_group_dn": "CN=WLAN-WORKNET,OU=Groups,DC=corp,DC=loc",
        "dc": "dc1",
    }

    # Verify reads user and confirms WLAN-WORKNET DN is in member_of_dns
    async def mock_get_user(login, server=None):
        return ADUserStatus(
            found=True,
            enabled=True,
            sam_account_name=login,
            groups=["Domain Users", "WLAN-WORKNET"],
            member_of_dns=[
                "CN=Domain Users,CN=Users,DC=corp,DC=loc",
                "CN=WLAN-WORKNET,OU=Groups,DC=corp,DC=loc",
            ],
            dc=server or "dc1",
        )

    executor.get_user_by_login_async.side_effect = mock_get_user
    handler = GrantWlanHandler(executor)
    ctx = HandlerContext(command_id="arch-4")
    res = await handler.run_pipeline(ctx, {"identity": "petrov.p.p"})
    assert res.success is True
    assert res.payload["verified"] is True
    assert res.payload["target_group"] == "WLAN-WORKNET"


# ---------------------------------------------------------------------------
# Archetype 5: WLAN access rejected on substring / similar group name
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_archetype_5_grant_wlan_similar_group_rejected():
    executor = AsyncMock()
    executor.get_user_status_async.return_value = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="petrov.p.p",
        groups=["Domain Users"],
        member_of_dns=["CN=Domain Users,CN=Users,DC=corp,DC=loc"],
    )
    executor.grant_wlan_access_async.return_value = {
        "success": True,
        "group": "WLAN-WORKNET",
        "target_group_dn": "CN=WLAN-WORKNET,OU=Groups,DC=corp,DC=loc",
    }

    # Post-read has WLAN-WORKNET-GUEST instead of WLAN-WORKNET
    async def mock_get_user(login, server=None):
        return ADUserStatus(
            found=True,
            enabled=True,
            sam_account_name=login,
            groups=["Domain Users", "WLAN-WORKNET-GUEST"],
            member_of_dns=[
                "CN=Domain Users,CN=Users,DC=corp,DC=loc",
                "CN=WLAN-WORKNET-GUEST,OU=Groups,DC=corp,DC=loc",
            ],
        )

    executor.get_user_by_login_async.side_effect = mock_get_user
    handler = GrantWlanHandler(executor)
    ctx = HandlerContext(command_id="arch-5")
    res = await handler.run_pipeline(ctx, {"identity": "petrov.p.p"})
    assert res.success is False
    assert res.failure_kind == "verification_failed"
    assert res.failure_code in ("grant_wlan_verification_failed", "membership_unverified")


# ---------------------------------------------------------------------------
# Archetype 6: Printer install matched on action intent
# ---------------------------------------------------------------------------
def test_archetype_6_printer_install_action_matched():
    text = "Прошу установить сетевой принтер на рабочее место ZTE1000"
    facts = merge_observations([
        _obs("subject", text, FactSource.STRUCTURED_FIELD, "ticket:subject"),
        _obs("description", text, FactSource.STRUCTURED_FIELD, "ticket:description"),
    ])
    ctx = ScenarioContext(task={"Id": 6, "Name": text, "Description": text}, facts=facts)
    matched, conf, _ = _printer_install_match(ctx)
    assert matched is True
    assert conf > 0.9


# ---------------------------------------------------------------------------
# Archetype 7: Printer install rejected on "completed" intent
# ---------------------------------------------------------------------------
def test_archetype_7_printer_completed_intent_rejected():
    text = "Принтер уже установлен, но закончился картридж"
    facts = merge_observations([
        _obs("subject", text, FactSource.STRUCTURED_FIELD, "ticket:subject"),
        _obs("description", text, FactSource.STRUCTURED_FIELD, "ticket:description"),
    ])
    ctx = ScenarioContext(task={"Id": 7, "Name": text, "Description": text}, facts=facts)
    matched, _, reason = _printer_install_match(ctx)
    assert matched is False
    assert reason == "intent_completed"


# ---------------------------------------------------------------------------
# Archetype 8: Printer install rejected on "negation" intent
# ---------------------------------------------------------------------------
def test_archetype_8_printer_negation_intent_rejected():
    text = "Не нужно устанавливать принтер, заявка закрыта"
    facts = merge_observations([
        _obs("subject", text, FactSource.STRUCTURED_FIELD, "ticket:subject"),
        _obs("description", text, FactSource.STRUCTURED_FIELD, "ticket:description"),
    ])
    ctx = ScenarioContext(task={"Id": 8, "Name": text, "Description": text}, facts=facts)
    matched, _, reason = _printer_install_match(ctx)
    assert matched is False
    assert reason == "intent_negated"


# ---------------------------------------------------------------------------
# Archetype 9: Printer install rejected on "symptom" intent
# ---------------------------------------------------------------------------
def test_archetype_9_printer_symptom_intent_rejected():
    text = "Принтер в кабинете 204 не печатает, полосит"
    facts = merge_observations([
        _obs("subject", text, FactSource.STRUCTURED_FIELD, "ticket:subject"),
        _obs("description", text, FactSource.STRUCTURED_FIELD, "ticket:description"),
    ])
    ctx = ScenarioContext(task={"Id": 9, "Name": text, "Description": text}, facts=facts)
    matched, _, reason = _printer_install_match(ctx)
    assert matched is False
    assert "intent_symptom" in reason


# ---------------------------------------------------------------------------
# Archetype 10: Applicant comment correction supersedes structured field
# ---------------------------------------------------------------------------
def test_archetype_10_applicant_correction_supersedes_structured_field():
    obs = [
        _obs("pc_name", "ZTE1111", FactSource.STRUCTURED_FIELD, "field:pc"),
        _obs(
            "pc_name",
            "ZTE2222",
            FactSource.COMMENT,
            "comment:1:pc",
            metadata={"is_correction": True, "comment_text": "Ошибся, правильный компьютер ZTE2222"},
        ),
    ]
    facts = merge_observations(obs)
    assert facts.valid_value("pc_name") == "ZTE2222"


# ---------------------------------------------------------------------------
# Archetype 11: Normalizer rejects invalid IPv4 & loopback
# ---------------------------------------------------------------------------
def test_archetype_11_normalizer_invalid_ipv4_and_scopes():
    assert parse_printer_address("10.244.300.20").is_valid is False
    assert parse_printer_address("127.0.0.1").is_valid is False
    assert parse_printer_address("127.0.0.1").error == "unsupported_ip_scope"
    assert parse_pc_name("ztep1000").error == "printer_prefix_not_pc"


# ---------------------------------------------------------------------------
# Archetype 12: Fact grounding rejects hallucinated evidence
# ---------------------------------------------------------------------------
def test_archetype_12_fact_grounding_rejects_hallucination():
    full_text = "Заявка на доступ для Сидорова"
    facts = ExtractedTicketFacts(
        schema_version=1,
        pc_name="ZTE9999",
        evidence=[
            Evidence(
                source="llm",
                field="pc_name",
                code="quoted",
                span="ZTE9999",
            )
        ],
    )
    ok, err = validate_extracted_grounding(facts, full_text)
    assert ok is False
    assert err == "ungrounded_evidence_span:pc_name"
