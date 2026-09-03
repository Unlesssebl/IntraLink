import pytest

from app.utils.intraservice_parser import (
    enrich_task_data,
    parse_custom_fields,
)
from app.utils.normalizer import (
    is_valid_pc_name,
    is_valid_printer_name,
    normalize_pc_name,
    normalize_printer_address,
    resolve_pc_candidates,
    resolve_printer_candidates,
)


def test_normalize_pc_name():
    assert normalize_pc_name("зтэ1234") == "ZTE1234"
    assert normalize_pc_name("ZTE-1234") == "ZTE1234"
    assert normalize_pc_name("ткт1005") == "TKT1005"
    assert normalize_pc_name("ntemw0505") == "NTEMW0505"
    assert normalize_pc_name("нет номера") is None
    assert normalize_pc_name("компьютер") is None


def test_normalize_printer_address():
    assert normalize_printer_address("10,244,1,20") == "10.244.1.20"
    assert normalize_printer_address("10.244 1.20") == "10.244.1.20"
    assert normalize_printer_address("кзмп1010") == "kzmp1010"
    assert normalize_printer_address("ztep0012") == "ztep0012"
    assert normalize_printer_address("itt1000") == "ittp1000"


def test_is_valid_pc_and_printer_name():
    assert is_valid_pc_name("ZTE1234") is True
    assert is_valid_pc_name("KZM0505") is True
    assert is_valid_pc_name("invalid") is False
    assert is_valid_pc_name("1234") is False

    assert is_valid_printer_name("kzmp1234") is True
    assert is_valid_printer_name("ztep0505") is True
    assert is_valid_printer_name("invalid_prn") is False


def test_resolve_pc_candidates():
    cands = resolve_pc_candidates("1234", company="ПТФК ЗТЭО", dept="КИЦ")
    assert "ZTE1234" in cands
    assert "NTEMW1234" in cands

    cands_kzm = resolve_pc_candidates("0500", company="КЗМК ТЭМПО")
    assert "KZM0500" in cands_kzm


def test_resolve_printer_candidates():
    cands = resolve_printer_candidates("1010", company="КЗМК ТЭМПО")
    assert "kzmp1010" in cands
    assert "ittp1010" in cands


def test_parse_custom_fields_xml():
    xml_data = """
    <fields>
        <field id="1087">АБК-3, каб. 112</field>
        <field id="1088">49-87</field>
        <field id="1089">зтэ-1234</field>
        <field id="1091">Отдел АСУ</field>
        <field id="1092">Иванов Иван</field>
    </fields>
    """
    res = parse_custom_fields(xml_data)
    assert res["pc_name"] == "ZTE1234"
    assert res["phone"] == "49-87"
    assert res["room"] == "АБК-3, каб. 112"
    assert res["department"] == "Отдел АСУ"
    assert res["user_name"] == "Иванов Иван"
    assert "Имя ПК" in res["friendly"]
    assert res["friendly"]["Имя ПК"] == "зтэ-1234"


def test_parse_custom_fields_heuristics():
    xml_data = """
    <fields>
        <field id="9999">ZTE9999</field>
        <field id="8888">+79991234567</field>
        <field id="7777">user@tempo.org</field>
        <field id="6666">Кабинет 205</field>
    </fields>
    """
    res = parse_custom_fields(xml_data)
    assert res["pc_name"] == "ZTE9999"
    assert res["phone"] == "+79991234567"
    assert res["email"] == "user@tempo.org"
    assert res["room"] == "Кабинет 205"


def test_enrich_task_data():
    task = {
        "Id": 100,
        "Name": "Настройка ПК",
        "Data": '<fields><field id="1089">kzm-0555</field></fields>',
        "Attachments": [{"FileName": "screen.png", "Id": 1}],
    }
    enriched = enrich_task_data(task)
    assert enriched is not None
    assert "_parsed_fields" in enriched
    assert "_field_meta" in enriched
    assert enriched["_field_meta"]["pc_name"] == "KZM0555"
    assert enriched["_has_attachments"] is True
    assert len(enriched["_attachments_list"]) == 1
