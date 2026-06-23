import pytest
from worker_services.indexer_service import (
    derive_series_name_from_models,
    get_arch_score,
    is_valid_inf_path
)

def test_is_valid_inf_path():
    assert is_valid_inf_path("path/to/driver.inf") is True
    assert is_valid_inf_path("path/to/winxp/driver.inf") is False
    assert is_valid_inf_path("path/to/win2000/driver.inf") is False
    assert is_valid_inf_path("path/to/driver_nt4.inf") is False

def test_get_arch_score():
    assert get_arch_score("path/x64/driver.inf") == 10
    assert get_arch_score("path/win32/driver.inf") == 1
    assert get_arch_score("path/some_arch/driver.inf") == 5

def test_derive_series_name_from_models_single():
    models = {"HP LaserJet M1120 MFP"}
    assert derive_series_name_from_models(models) == "HP_LaserJet_M1120_MFP"

def test_derive_series_name_from_models_multiple():
    models = {"HP LaserJet M253", "HP LaserJet M254"}
    assert derive_series_name_from_models(models) == "HP_LaserJet_M253-M254"

def test_derive_series_name_from_models_many():
    models = {
        "HP LaserJet 1018",
        "HP LaserJet 1020",
        "HP LaserJet 1022",
        "HP LaserJet 1025"
    }
    # Так как их 4, они перечислятся через дефис:
    assert derive_series_name_from_models(models) == "HP_LaserJet_1018-1020-1022-1025"

def test_derive_series_name_from_models_too_many():
    models = {
        "HP LaserJet M129",
        "HP LaserJet M130",
        "HP LaserJet M131",
        "HP LaserJet M132",
        "HP LaserJet M134"
    }
    # Так как их > 4, используется диапазон:
    assert derive_series_name_from_models(models) == "HP_LaserJet_M129-M134"

def test_derive_series_name_from_models_clean_suffixes():
    models = {"HP LaserJet Pro MFP M225 PCL 6", "HP LaserJet Pro MFP M226 PCL6"}
    assert derive_series_name_from_models(models) == "HP_LaserJet_Pro_MFP_M225-M226"

def test_derive_series_name_from_models_empty():
    assert derive_series_name_from_models(set()) is None
