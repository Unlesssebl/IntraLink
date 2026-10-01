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
    # Contextual labels with known corporate prefixes
    assert extract_pc_names_from_text("Компьютер: KZM0010") == ["KZM0010"]
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


def test_enterprise_pc_prefixes_kzm_tnt_cyrillic():
    """Verify that enterprise prefixes (KZM, TNT, ZTE, KMK, etc.) are accurately parsed and normalized."""
    assert extract_pc_names_from_text("принтер подключен ПК kzm0010") == ["KZM0010"]
    assert extract_pc_names_from_text("принтер подключен к пк кзм0010") == ["KZM0010"]
    assert extract_pc_names_from_text("на ПК КЗМ 0010") == ["KZM0010"]
    assert extract_pc_names_from_text("установить принтер на тнт0088") == ["TNT0088"]
    assert extract_pc_names_from_text("компьютер ТНТ 0088") == ["TNT0088"]
    assert extract_pc_names_from_text("kzm0010") == ["KZM0010"]


def test_printer_queue_vs_pc_differentiation():
    """Verify that printer queues with suffix 'P' (KZMP, SCSP, ITTP) are not parsed as workstations."""
    assert extract_pc_names_from_text("принтер KZMP0010") == []
    assert extract_pc_names_from_text("настроить МФУ SCSP 0001") == []


def test_no_false_positive_verb_as_hostname():
    """Regression test for ticket #141927: verbs/adjectives after marker words must NOT be treated as hostnames.

    Root cause: PC_CONTEXT_REGEX captured the next word after 'хост'/'пк'/'компьютер' without
    checking is_valid_pc_name(), and normalize_pc_name() returned cleaned.upper() for purely-
    alphabetic tokens instead of an empty string.  Both bugs are now fixed.
    """
    # The exact pattern that produced "стали" as pc_name on ticket #141927
    false_positive_phrases = [
        "хост стали",
        "хост: стали",
        "Недоступен хост стали",
        "Устройства стали недоступны, хост стали",
        "ПК стали недоступны",
        "компьютер стали не запускается",
        "устройства стали недоступны",
        "принтеры стали недоступны после обновления",
        "хост недоступны",
        "пк недоступны",
    ]
    for phrase in false_positive_phrases:
        result = extract_pc_names_from_text(phrase)
        assert result == [], f"False positive for {repr(phrase)!r}: got {result}"

    # Sanity: valid hostnames immediately after markers still work
    assert extract_pc_names_from_text("хост: KZM0010") == ["KZM0010"]
    assert extract_pc_names_from_text("пк kzm0010") == ["KZM0010"]
    assert extract_pc_names_from_text("ПК WKS-1020") == ["WKS-1020"]


def test_normalize_pc_name_no_digit_returns_empty():
    """normalize_pc_name must return empty string for purely-alphabetic tokens (no digit part)."""
    assert normalize_pc_name("стали") == ""
    assert normalize_pc_name("недоступны") == ""
    assert normalize_pc_name("компьютер") == ""
    assert normalize_pc_name("принтер") == ""
    # Valid tokens must still work
    assert normalize_pc_name("KZM0010") == "KZM0010"
    assert normalize_pc_name("wks1020") == "WKS-1020"


def test_enrich_task_dict_reads_data_xml_and_direct_field_keys():
    """Verify that Data XML and direct Field1057..Field1068 fields (as returned by real IntraService API in ticket 142250) are parsed."""
    task_142250_raw = {
        "Id": 142250,
        "Name": "Создание пользователя",
        "Description": "Создать учетную запись",
        "TypeId": 2,
        "ServiceId": 53,
        "Creator": "Беликов Ален",
        "CreatorId": 8664,
        "Data": (
            "<fields>"
            "<field id='1057'>Ахмедж</field>"
            "<field id='1058'>Ахмедж</field>"
            "<field id='1059'>Ахмедж</field>"
            "<field id='1064'>Ахмедж</field>"
            "<field id='1065'>Ахмедж</field>"
            "<field id='1066'>Ахмедж</field>"
            "<field id='1068'>WKS-1020</field>"
            "</fields>"
        ),
        "Field1057": "Ахмедж",
        "Field1058": "Ахмедж",
        "Field1059": "Ахмедж",
        "Field1064": "Ахмедж",
        "Field1065": "Ахмедж",
        "Field1066": "Ахмедж",
        "Field1068": "WKS-1020",
    }
    enriched = enrich_task_dict(task_142250_raw)
    assert enriched["entities"]["last_name"] == "Ахмедж"
    assert enriched["entities"]["first_name"] == "Ахмедж"
    assert enriched["entities"]["middle_name"] == "Ахмедж"
    assert enriched["entities"]["department"] == "Ахмедж"
    assert enriched["entities"]["title"] == "Ахмедж"
    assert enriched["entities"]["phone"] == "Ахмедж"
    assert enriched["entities"]["pc_name"] == "WKS-1020"
    assert enriched["TaskTypeId"] == 2
    assert enriched["CustomFieldData"] == task_142250_raw["Data"]
    assert "Фамилия" in enriched["custom_fields"]
    assert "Имя" in enriched["custom_fields"]
    assert "Отчество" in enriched["custom_fields"]
    assert "Подразделение" in enriched["custom_fields"]
    assert "Должность" in enriched["custom_fields"]


def test_enrich_task_dict_categories_and_creator_company_fallback():
    """Verify that categories and creator metadata are captured and company falls back to categories."""
    raw_task = {
        "Id": 142536,
        "Name": "Заявка на создание пользователя сети",
        "Description": "",
        "ServiceId": 53,
        "TypeId": 2,
        "Categories": 'АО "НТЗ "ТЭМ-ПО"',
        "CategoryIds": "57",
        "Creator": "Урошникова Далила",
        "CreatorId": 10021,
        "CreatorCompanyName": "АО НТЗ ТЭМПО",
        "CreatorLogin": "uroshnikova.d.v",
        "CreatorADGuid": "f7088ba9-110a-4151-a073-5909a7b1dc16",
        "Data": (
            "<fields>"
            "<field id='1057'>Фаррахова</field>"
            "<field id='1058'>Чулпан</field>"
            "<field id='1059'>Вахитовна</field>"
            "<field id='1064'>цеха №4</field>"
            "<field id='1065'>Кладовщик</field>"
            "<field id='1066'>7412</field>"
            "<field id='1068'>1260</field>"
            "</fields>"
        ),
    }
    enriched = enrich_task_dict(raw_task)
    assert enriched["entities"]["last_name"] == "Фаррахова"
    assert enriched["entities"]["first_name"] == "Чулпан"
    assert enriched["entities"]["middle_name"] == "Вахитовна"
    assert enriched["entities"]["department"] == "цеха №4"
    assert enriched["entities"]["title"] == "Кладовщик"
    assert enriched["entities"]["company"] == "АО НТЗ ТЭМ-ПО"
    assert enriched["entities"]["categories"] == 'АО "НТЗ "ТЭМ-ПО"'
    assert enriched["entities"]["applicant_company"] == "АО НТЗ ТЭМПО"
    assert enriched["entities"]["applicant_login"] == "uroshnikova.d.v"
    assert enriched["entities"]["applicant_ad_guid"] == "f7088ba9-110a-4151-a073-5909a7b1dc16"
    assert enriched["categories"] == 'АО "НТЗ "ТЭМ-ПО"'
    assert enriched["creator_company_name"] == "АО НТЗ ТЭМПО"
    assert enriched["creator_login"] == "uroshnikova.d.v"

