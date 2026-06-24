import pytest
from orchestrator.device_normalizer import normalize_pc_name, normalize_printer_address

def test_normalize_pc_name_basic():
    # Простые случаи
    assert normalize_pc_name("ntemw0002") == "NTEMW0002"
    assert normalize_pc_name("TKT1234") == "TKT1234"
    assert normalize_pc_name(None) is None
    assert normalize_pc_name("") is None

def test_normalize_pc_name_homoglyphs():
    # Кириллица на латиницу (русские "Т", "Е", "М" в NTEМW)
    # русская "Т" = \u0422, русская "М" = \u041c
    assert normalize_pc_name("N\u0422E\u041cW0002") == "NTEMW0002"

def test_normalize_pc_name_fuzzy():
    # Пропущена буква 'M'
    assert normalize_pc_name("ntew0002") == "NTEMW0002"
    # Лишняя буква
    assert normalize_pc_name("ntemww0002") == "NTEMW0002"
    # Замена буквы
    assert normalize_pc_name("nterw0002") == "NTEMW0002"
    # Короткий префикс
    assert normalize_pc_name("tk0001") == "TKT0001"
    # Без префикса (просто цифры) - должны остаться цифрами
    assert normalize_pc_name("1684") == "1684"

def test_normalize_printer_address_ip():
    # IP адрес не меняется
    assert normalize_printer_address("10.244.1.20") == "10.244.1.20"
    # Опечатки с запятыми и пробелами исправляются
    assert normalize_printer_address("10,244 1.20") == "10.244.1.20"

def test_normalize_printer_address_dns():
    # DNS-имя в нижнем регистре
    assert normalize_printer_address("itp0001") == "ittp0001"
    assert normalize_printer_address("kzmp0002") == "kzmp0002"
    # Ошибка в KMKP (kmk -> kmkp)
    assert normalize_printer_address("kmk0001") == "kmkp0001"
    # Омоглифы в DNS-имени
    # русская "а" в kzmp
    assert normalize_printer_address("kz\u0430p0002") == "kzmp0002"
