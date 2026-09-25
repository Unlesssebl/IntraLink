"""Tests for IntraService custom fields parsing and onboarding entities extraction."""

from unittest.mock import AsyncMock, MagicMock
import json
import pytest

from core.intraservice.dto import ExtractedEntitiesDTO
from core.intraservice.parser import enrich_task_dict, enrich_task_dict_async, parse_custom_fields


def test_parse_custom_fields_form_1018():
    """Verify parsing of Directum User Account form (Type 1018) via Tier 1 XML."""
    xml_data = """
    <fields>
        <field id="1121">Кузнецов</field>
        <field id="1122">Михаил</field>
        <field id="1123">Сергеевич</field>
        <field id="1128">Отдел системного администрирования</field>
        <field id="1129">Ведущий инженер</field>
        <field id="1130">+7 (999) 111-22-33</field>
        <field id="1132">WKS-8899</field>
        <field id="1133">44556</field>
        <field id="1134">Иванов И.И.</field>
        <field id="1135">Согласование договоров</field>
        <field id="1180">Да</field>
        <field id="1488">kuznetsov.m</field>
        <field id="1489">TempPass123!</field>
        <field id="1521">kuznetsov@corporate.loc</field>
        <field id="1017">ООО Ромашка</field>
        <field id="1181">DIR-99001</field>
    </fields>
    """
    entities, friendly = parse_custom_fields(xml_data)

    assert isinstance(entities, ExtractedEntitiesDTO)
    assert entities.last_name == "Кузнецов"
    assert entities.first_name == "Михаил"
    assert entities.middle_name == "Сергеевич"
    assert entities.user_name == "Кузнецов Михаил Сергеевич"
    assert entities.department == "Отдел системного администрирования"
    assert entities.title == "Ведущий инженер"
    assert entities.phone == "+7 (999) 111-22-33"
    assert entities.pc_name == "WKS-8899"
    assert entities.tab_number == "44556"
    assert entities.similar_user == "Иванов И.И."
    assert entities.directum_actions == "Согласование договоров"
    assert entities.install_directum == "Да"
    assert entities.it_login == "kuznetsov.m"
    assert entities.it_password == "TempPass123!"
    assert entities.email == "kuznetsov@corporate.loc"
    assert entities.company == "ООО Ромашка"
    assert entities.directum_task_id == "DIR-99001"
    assert entities.target_user == "kuznetsov.m"


def test_enrich_task_dict_tier3_hardware_fallbacks():
    """Verify Tier 3 fast deterministic fallbacks for hardware host, phone, and login."""
    task = {
        "Name": "Настройка рабочего места",
        "Description": "Логин: vasiliev.d, ПК: WS-ACC-01, тел. +7 (495) 123-45-67",
        "CustomFieldData": None,
    }
    enriched = enrich_task_dict(task)
    ent = enriched["entities"]

    assert ent["pc_name"] == "WS-ACC-01"
    assert ent["target_user"] == "vasiliev.d"
    assert ent["phone"] == "+7 (495) 123-45-67"


@pytest.mark.asyncio
async def test_enrich_task_dict_async_ai_pipeline():
    """Verify Tier 2 AI extraction seamlessly populates missing employee facts."""
    mock_ai = AsyncMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({
        "last_name": "Васильев",
        "first_name": "Дмитрий",
        "middle_name": "Олегович",
        "user_name": "Васильев Дмитрий Олегович",
        "tab_number": "887766",
        "title": "Главный бухгалтер",
        "company": "АО Корпорация",
        "similar_user": "Петров П.П.",
        "room": "каб. 402",
    })
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_ai.chat.completions.create.return_value = mock_resp

    task = {
        "Name": "Создать учетку сотрудника",
        "Description": (
            "Сотрудник: Васильев Дмитрий Олегович, таб. номер: 887766. "
            "Должность: Главный бухгалтер. Организация: АО Корпорация. "
            "Со схожими правами: Петров П.П. "
            "ПК: WS-ACC-01, каб. 402, тел. +7 (495) 123-45-67"
        ),
        "CustomFieldData": None,
    }
    enriched = await enrich_task_dict_async(task, ai_client=mock_ai)
    ent = enriched["entities"]

    assert ent["last_name"] == "Васильев"
    assert ent["first_name"] == "Дмитрий"
    assert ent["middle_name"] == "Олегович"
    assert ent["user_name"] == "Васильев Дмитрий Олегович"
    assert ent["tab_number"] == "887766"
    assert ent["title"] == "Главный бухгалтер"
    assert ent["company"] == "АО Корпорация"
    assert ent["similar_user"] == "Петров П.П."
    assert ent["pc_name"] == "WS-ACC-01"
    assert ent["room"] == "каб. 402"
    assert ent["phone"] == "+7 (495) 123-45-67"
