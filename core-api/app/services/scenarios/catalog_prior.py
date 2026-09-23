"""Catalog Prior Provider: maps IntraService catalog metadata to scenario hypotheses."""

from __future__ import annotations

import logging
from typing import Any

from app.services.scenarios.contracts import PriorHypothesis
from shared.domain import FactBag

logger = logging.getLogger("core_api.scenarios.catalog_prior")

# Статический реестр соответствия ServiceId -> (scenario_key, scenario_version)
SERVICE_TO_SCENARIO: dict[int, tuple[str, int]] = {
    # 01. Учетные записи пользователей
    53: ("create_user", 1),       # Создание нового пользователя сети
    42: ("create_user", 1),       # Учетные записи (корень)
    54: ("create_user", 1),       # Блокировка/разблокировка
    55: ("create_user", 1),       # Смена прав/подразделения
    124: ("create_user", 1),
    104: ("create_user", 1),
    186: ("create_user", 1),
    63: ("grant_wlan", 1),        # Wi-Fi доступ (WLAN-WORKNET)

    # 02. Программы и ОС
    58: ("os_reinstallation", 1), # Переустановка ОС
    59: ("pc_performance", 1),   # Медленная работа ПК

    # 03. Оргтехника и ПК
    82: ("install_printer", 2),   # Установка принтера
    83: ("install_printer", 2),   # Подключение принтера
    88: ("physical_device", 1),   # Ремонт оборудования (системный блок, монитор)
    112: ("physical_device", 1),  # Сервисный центр / железо
    113: ("physical_device", 1),

    # 04. Сеть и интернет
    20: ("network_diagnostics", 1),
    43: ("network_diagnostics", 1),
    71: ("network_diagnostics", 1),
    181: ("network_diagnostics", 1),
}

# Соответствие TaskTypeId -> (scenario_key, scenario_version)
TASK_TYPE_TO_SCENARIO: dict[int, tuple[str, int]] = {
    1018: ("create_user", 1),     # Заявка на пользователя DIRECTUM
}


class CatalogPriorProvider:
    """
    Поставщик априорного выбора сценария.
    Анализирует ServiceId, TaskTypeId, ServiceParentId и формирует PriorHypothesis.
    """

    def __init__(self, custom_mapping: dict[int, tuple[str, int]] | None = None) -> None:
        self._service_map = dict(SERVICE_TO_SCENARIO)
        if custom_mapping:
            self._service_map.update(custom_mapping)

    def get_prior(
        self,
        task: dict[str, Any],
        facts: FactBag | None = None,
    ) -> PriorHypothesis | None:
        # 1. Проверяем точный ServiceId
        service_id_raw = task.get("ServiceId") or task.get("service_id")
        if service_id_raw is not None:
            try:
                sid = int(service_id_raw)
                if sid in self._service_map:
                    scenario_key, version = self._service_map[sid]
                    return PriorHypothesis(
                        scenario_key=scenario_key,
                        scenario_version=version,
                        confidence=0.95,
                        source="service_id",
                        reason=f"service_id:{sid}",
                    )
            except (ValueError, TypeError):
                pass

        # 2. Проверяем TaskTypeId (специализированные регламентные формы)
        task_type_id_raw = task.get("TaskTypeId") or task.get("task_type_id")
        if task_type_id_raw is not None:
            try:
                ttid = int(task_type_id_raw)
                if ttid in TASK_TYPE_TO_SCENARIO:
                    scenario_key, version = TASK_TYPE_TO_SCENARIO[ttid]
                    return PriorHypothesis(
                        scenario_key=scenario_key,
                        scenario_version=version,
                        confidence=0.95,
                        source="task_type",
                        reason=f"task_type_id:{ttid}",
                    )
            except (ValueError, TypeError):
                pass

        # 3. Проверяем ServiceParentId (наследование от родительского сервиса)
        parent_id_raw = task.get("ServiceParentId") or task.get("service_parent_id")
        if parent_id_raw is not None:
            try:
                pid = int(parent_id_raw)
                # Раздел 01: Учетные записи (кроме Wi-Fi)
                if pid == 42:
                    return PriorHypothesis(
                        scenario_key="create_user",
                        scenario_version=1,
                        confidence=0.90,
                        source="service_parent",
                        reason=f"parent_service_id:{pid}",
                    )
            except (ValueError, TypeError):
                pass

        # Априорная гипотеза отсутствует (unanchored ticket)
        return None
