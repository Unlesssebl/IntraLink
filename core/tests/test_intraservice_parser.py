"""Test IntraService custom field XML parsing and normalization."""

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
