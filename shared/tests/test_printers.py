import pytest
from shared.printers import (
    load_printers_kb,
    find_printer_by_name,
    PrintersKnowledgeBase,
    PrinterConfig,
)


def test_load_printers_kb():
    kb = load_printers_kb(force_reload=True)
    assert isinstance(kb, PrintersKnowledgeBase)
    assert len(kb.printers) >= 3
    assert "hp_universal_upd" in [p.model_key for p in kb.printers]
    assert "kyocera_kx_upd" in [p.model_key for p in kb.printers]


def test_find_printer_by_name_hp():
    p = find_printer_by_name("HP LaserJet M402dne")
    assert p is not None
    assert p.vendor == "hp"
    assert "hpcu360u.inf" in p.driver_inf_path


def test_find_printer_by_name_kyocera():
    p = find_printer_by_name("Kyocera ECOSYS P2040dn")
    assert p is not None
    assert p.vendor == "kyocera"
    assert "OEMSETUP.INF" in p.driver_inf_path


def test_find_printer_by_name_xerox():
    p = find_printer_by_name("Xerox B210")
    assert p is not None
    assert p.vendor == "xerox"
    assert p.connection_type == "usb"
