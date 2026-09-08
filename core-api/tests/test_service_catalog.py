import pytest
from app.services.service_catalog import ServiceCatalogService, ServiceInfo


def test_build_catalog_map_hierarchical_paths():
    """Проверяет построение полного пути сервиса из сырых данных IntraService API."""
    mock_raw = [
        {"Id": 42, "Name": "01. Учетные записи пользователей", "ParentId": None, "Path": "42|"},
        {"Id": 53, "Name": "Создание учетной записи", "ParentId": 42, "Path": "42|53|"},
        {"Id": 63, "Name": "Создание почты", "ParentId": 53, "Path": "42|53|63|"},
        {"Id": 16, "Name": "11. Общие вопросы", "ParentId": None, "Path": "16|"},
    ]

    catalog = ServiceCatalogService.build_catalog_map(mock_raw)
    assert len(catalog) == 4

    # 1. Корневой сервис
    root = catalog[42]
    assert root.service_path == "01. Учетные записи пользователей"
    assert root.path_ids == (42,)
    assert root.path_ids_str == "42|"
    assert root.root_num == "01"

    # 2. Уровень 2
    sub = catalog[53]
    assert sub.service_path == "01. Учетные записи пользователей / Создание учетной записи"
    assert sub.path_ids == (42, 53)
    assert sub.path_ids_str == "42|53|"
    assert sub.root_num == "01"
    assert sub.root_id == 42

    # 3. Уровень 3
    leaf = catalog[63]
    assert leaf.service_path == "01. Учетные записи пользователей / Создание учетной записи / Создание почты"
    assert leaf.path_ids == (42, 53, 63)
    assert leaf.path_ids_str == "42|53|63|"
    assert leaf.root_num == "01"
    assert leaf.root_id == 42


def test_build_catalog_map_without_path_field():
    """Проверяет рекурсивное восстановление пути по ParentId, если поля Path нет."""
    mock_raw = [
        {"Id": 19, "Name": "03. Оргтехника", "ParentId": None},
        {"Id": 32, "Name": "Принтеры и МФУ", "ParentId": 19},
        {"Id": 183, "Name": "Сетевой принтер HP", "ParentId": 32},
    ]

    catalog = ServiceCatalogService.build_catalog_map(mock_raw)
    leaf = catalog[183]
    assert leaf.service_path == "03. Оргтехника / Принтеры и МФУ / Сетевой принтер HP"
    assert leaf.path_ids == (19, 32, 183)
    assert leaf.root_id == 19


@pytest.mark.asyncio
async def test_service_catalog_fallback_unknown_service():
    """Проверяет отказоустойчивость при неизвестном сервисе (Cold start)."""
    # 53 известен в статике как раздел 01
    info = await ServiceCatalogService.get_service_info(53)
    assert info is not None
    assert "01." in info.service_path

    # Полностью неизвестный ID
    info_unk = await ServiceCatalogService.get_service_info(99999, service_name="Спец-сервис")
    assert info_unk is not None
    assert info_unk.service_path == "Спец-сервис"
    assert info_unk.path_ids == (99999,)


def test_normalize_path_length_budget():
    """Проверяет обрезку сверхдлинных путей для Cross-Encoder бюджета токенов."""
    parts = ["А" * 50, "Б" * 50, "В" * 50]
    norm = ServiceCatalogService.normalize_path_string(parts)
    assert len(norm) <= 120
