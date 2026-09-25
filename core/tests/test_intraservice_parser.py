"""Test IntraService custom field XML parsing and normalization."""

import pytest

from core.intraservice.parser import (
    enrich_task_dict,
    extract_pc_names_from_text,
    normalize_pc_name,
    parse_custom_fields,
)


def test_normalize_pc_name():
    assert normalize_pc_name("  pc-102.corporate.loc  ") == "PC-102"
    assert normalize_pc_name("WS_FIN_04") == "WS_FIN_04"
    assert normalize_pc_name("") == ""


def test_extract_pc_names_from_text():
    text = "Прошу настроить принтер на рабочей станции PC-12345 и проверить доступ с WS-999"
    pcs = extract_pc_names_from_text(text)
    assert "PC-12345" in pcs
    assert "WS-999" in pcs


def test_parse_custom_fields_xml():
    xml = """
    <fields>
        <field id="1089">PC-BUHG-05</field>
        <field id="1088">45-21</field>
        <field id="1087">Кабинет 304</field>
        <field id="1091">Бухгалтерия</field>
        <field id="1092">Иванова Анна Сергеевна</field>
        <field id="1494">a.ivanova@company.loc</field>
    </fields>
    """
    entities, friendly = parse_custom_fields(xml)

    assert entities.pc_name == "PC-BUHG-05"
    assert entities.phone == "45-21"
    assert entities.room == "Кабинет 304"
    assert entities.department == "Бухгалтерия"
    assert entities.user_name == "Иванова Анна Сергеевна"
    assert entities.email == "a.ivanova@company.loc"

    assert friendly["Имя ПК"] == "PC-BUHG-05"
    assert friendly["Телефон"] == "45-21"


def test_enrich_task_dict_fallback_pc_detection():
    task_raw = {
        "Id": 1001,
        "Name": "Срочно переустановить 1С на PC-SALES-10",
        "Description": "Не запускается тонкий клиент",
        "CustomFieldData": None,
    }
    enriched = enrich_task_dict(task_raw)
    assert enriched["entities"]["pc_name"] == "PC-SALES-10"


def test_normalize_pc_name_wks_and_digits():
    assert normalize_pc_name("wks_1020") == "WKS-1020"
    assert normalize_pc_name("WKS1020") == "WKS-1020"
    assert normalize_pc_name("1020") == "WKS-1020"
    assert normalize_pc_name("ПК: 4521") == "WKS-4521"
    assert normalize_pc_name("  wks-99.corporate.loc  ") == "WKS-99"


def test_extract_printer_address_and_model_from_fields():
    xml = """
    <fields>
        <field id="1089">WKS-4020</field>
        <field id="1103">Kyocera Ecosys M2040dn</field>
        <field id="1104">10,244 1.25</field>
        <field id="1488">a.smirnov</field>
    </fields>
    """
    entities, friendly = parse_custom_fields(xml)
    assert entities.pc_name == "WKS-4020"
    assert entities.printer_model == "Kyocera Ecosys M2040dn"
    assert entities.printer_address == "10.244.1.25"
    assert entities.target_user == "a.smirnov"
    assert friendly["Модель принтера"] == "Kyocera Ecosys M2040dn"
    assert friendly["IP принтера"] == "10,244 1.25"


def test_extract_printer_address_and_model_from_text_fallback():
    task_raw = {
        "Id": 1002,
        "Name": "Подключить принтер HP LaserJet Pro M404dn к WKS-0050",
        "Description": "Сетевой адрес принтера 10.244.20.15, логин: user_test",
        "CustomFieldData": None,
    }
    enriched = enrich_task_dict(task_raw)
    assert enriched["entities"]["pc_name"] == "WKS-0050"
    assert enriched["entities"]["printer_model"] == "HP LaserJet Pro M404dn"
    assert enriched["entities"]["printer_address"] == "10.244.20.15"
    assert enriched["entities"]["target_user"] == "user_test"


def test_extract_printer_queue_name():
    task_raw = {
        "Id": 1003,
        "Name": "Не печатает МФУ SCSP 0001",
        "Description": "В бухгалтерии зависла очередь печати",
        "CustomFieldData": None,
    }
    enriched = enrich_task_dict(task_raw)
    assert enriched["entities"]["printer_address"] == "scsp0001"


def test_printer_models_not_treated_as_pc_hostname():
    """Verify that printer models (Brother, Pantum, Kyocera, Canon, HP, Ricoh) are never parsed as PC hostname."""
    printer_texts = [
        "Подключить DCP-L2500 в бухгалтерию",
        "Установить принтер Brother HL-1110",
        "Купили Pantum P3300dn для отдела кадров",
        "Купили Pantum P3300 для отдела кадров",
        "Заправка Kyocera FS-1040",
        "Настройка Lexmark C3326",
        "МФУ Canon MF3010 не сканирует",
        "Принтер HP LaserJet M404dn замял бумагу",
        "Настроить МФУ Ricoh SP210 в приемной",
    ]
    for text in printer_texts:
        pcs = extract_pc_names_from_text(text)
        assert not pcs, f"Failed for '{text}': parsed {pcs} as PC"


def test_strict_corporate_and_context_pc_extraction():
    """Verify standard corporate prefixes (WKS, NTEMW, ARM, PC, WS, NB) and contextual labels."""
    assert extract_pc_names_from_text("Настроить на WKS-1020 и NTEMW5540") == ["WKS-1020", "NTEMW5540"]
    assert extract_pc_names_from_text("Компьютер: buh-01") == ["BUH-01"]
    assert extract_pc_names_from_text("Печать на ПК 4521") == ["WKS-4521"]
    assert extract_pc_names_from_text("Рабочая станция PC-SALES-10") == ["PC-SALES-10"]
    assert extract_pc_names_from_text("Ноутбук NB-CHIEF-01") == ["NB-CHIEF-01"]


@pytest.mark.asyncio
async def test_filter_valid_pcs_async(monkeypatch):
    from core.intraservice.parser import filter_valid_pcs_async

    async def fake_validator(name, redis_client=None, ad_pool=None):
        return name in ("WKS-1020", "NTEMW5540")

    monkeypatch.setattr("core.ad.pool.is_valid_domain_computer", fake_validator)

    candidates = ["WKS-1020", "P3300", "NTEMW5540", "MF3010"]
    valid = await filter_valid_pcs_async(candidates)
    assert valid == ["WKS-1020", "NTEMW5540"]



