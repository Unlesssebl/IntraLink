import pytest
from shared.normalizer import (
    normalize_pc_name,
    normalize_printer_address,
    is_valid_pc_name,
    is_valid_printer_name,
    resolve_pc_candidates,
)
from shared.diagnostics import extract_potential_hosts
from shared.json_utils import json_dumps, json_loads


def test_normalize_pc_name():
    # Lowercase & uppercase normalization, strips dashes
    assert normalize_pc_name("zte-101") == "ZTE101"
    assert normalize_pc_name("  zte-002  ") == "ZTE002"
    # Cyrillic prefix normalization: ЗТЕ -> ZTE
    assert normalize_pc_name("ЗТЕ-123") == "ZTE123"


def test_is_valid_pc_name():
    assert is_valid_pc_name("ZTE-102") is True
    assert is_valid_pc_name("") is False
    assert is_valid_pc_name("A" * 65) is False


def test_is_valid_printer_name():
    assert is_valid_printer_name("ZTEP-01") is True
    assert is_valid_printer_name("") is False


def test_extract_potential_hosts():
    text = "Проблема на ПК ZTE-105 и сервере 192.168.1.100, проверьте."
    hosts = extract_potential_hosts(text)
    assert "ZTE-105" in hosts or "192.168.1.100" in hosts


def test_json_utils():
    data = {"key": "value", "list": [1, 2, 3], "flag": True}
    serialized = json_dumps(data)
    assert isinstance(serialized, str)
    deserialized = json_loads(serialized)
    assert deserialized == data
