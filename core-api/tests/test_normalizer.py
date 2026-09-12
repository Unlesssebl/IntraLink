import pytest

from shared.normalizer import (
    NormalizationResult,
    normalize_pc_name,
    normalize_printer_address,
    parse_pc_name,
    parse_printer_address,
)


def test_parse_pc_name_valid():
    res = parse_pc_name("зтэ1234")
    assert res.is_valid is True
    assert res.value == "ZTE1234"
    assert res.details["prefix"] == "ZTE"
    assert res.details["number"] == "1234"

    res_kzm = parse_pc_name("KZM-0505")
    assert res_kzm.is_valid is True
    assert res_kzm.value == "KZM0505"

    res_fqdn = parse_pc_name("ZTE1234.corporate.loc")
    assert res_fqdn.is_valid is True
    assert res_fqdn.value == "ZTE1234"


def test_parse_pc_name_invalid_cases():
    assert parse_pc_name("").is_valid is False
    assert parse_pc_name("   ").error == "empty_input"
    assert parse_pc_name("нет номера").error == "placeholder_value"
    assert parse_pc_name("компьютер").error == "placeholder_value"
    assert parse_pc_name("1234").error == "digits_only_requires_company_context"
    assert parse_pc_name("ztep1234").error == "printer_prefix_not_pc"
    assert parse_pc_name("XYZ9999").error == "unknown_pc_prefix"


def test_normalize_pc_name_backward_compatibility():
    assert normalize_pc_name("зтэ1234") == "ZTE1234"
    assert normalize_pc_name("нет номера") is None
    assert normalize_pc_name("1234") is None
    assert normalize_pc_name(None) is None


def test_parse_printer_address_ipv4():
    res = parse_printer_address("10.244.1.20")
    assert res.is_valid is True
    assert res.value == "10.244.1.20"
    assert res.details["type"] == "ipv4"

    # Permissive punctuation normalized to valid IPv4
    res_punct = parse_printer_address("10,244 1.20")
    assert res_punct.is_valid is True
    assert res_punct.value == "10.244.1.20"

    # Out of range octet
    res_bad = parse_printer_address("10.244.300.20")
    assert res_bad.is_valid is False
    assert "invalid_ipv4" in (res_bad.error or "")

    # Unsupported scopes (loopback, multicast, unspecified)
    assert parse_printer_address("127.0.0.1").error == "unsupported_ip_scope"
    assert parse_printer_address("224.0.0.1").error == "unsupported_ip_scope"
    assert parse_printer_address("0.0.0.0").error == "unsupported_ip_scope"


def test_parse_printer_address_hostnames():
    res_scsp = parse_printer_address("SCSP 0001")
    assert res_scsp.is_valid is True
    assert res_scsp.value == "scsp0001"
    assert res_scsp.details["type"] == "hostname"

    res_cyr = parse_printer_address("кзмп1010")
    assert res_cyr.is_valid is True
    assert res_cyr.value == "kzmp1010"


def test_normalize_printer_address_backward_compatibility():
    assert normalize_printer_address("10,244,1,20") == "10.244.1.20"
    assert normalize_printer_address("10.244.300.20") is None
    assert normalize_printer_address("127.0.0.1") is None
    assert normalize_printer_address(None) is None
