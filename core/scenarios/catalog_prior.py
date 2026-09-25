"""Catalog-First Prior Provider for IntraLink v2 Autopilot.

Maps structured ticket attributes (ServiceId, ServiceName, TaskTypeId, TaskTypeName)
to an a-priori scenario hypothesis cluster with high initial confidence (P0 >= 0.90).
"""

import logging
from typing import Dict, Optional, Tuple

from core.intraservice.catalog import (
    SERVICE_IDS_ACCOUNT_CREATE,
    SERVICE_IDS_ACCOUNT_LOCK,
    SERVICE_IDS_OFFLINE_HOST,
    SERVICE_IDS_PRINTER_INSTALL,
    SERVICE_IDS_PRINTER_SUPPORT,
    SERVICE_IDS_WLAN,
)
from core.intraservice.dto import TaskDTO

logger = logging.getLogger("core.scenarios.catalog_prior")

# Known exact service ID mappings for specific/singular services
SERVICE_ID_MAPPINGS: Dict[int, Tuple[str, float, str]] = {
    # 55 = Заявка на пользователя Directum
    55: ("account_create", 0.95, "ServiceId 55 (Directum User Account Provisioning)"),
    # 232 = Доступ к корпоративным системам
    232: ("account_create", 0.95, "ServiceId 232 (Corporate Systems Access Provisioning)"),
    # 8 = Увольнение / блокировка доступа
    8: ("account_lock", 0.95, "ServiceId 8 (Employee Offboarding / Account Lock)"),
    # 19, 62, 82, 83, 183 = Установка и настройка принтеров / МФУ
    19: ("install_printer", 0.95, "ServiceId 19 (Обслуживание оргтехники и заправка картриджей)"),
    62: ("install_printer", 0.95, "ServiceId 62 (Printers/MFU installation)"),
    82: ("install_printer", 0.95, "ServiceId 82 (Printer Setup)"),
    83: ("install_printer", 0.95, "ServiceId 83 (MFU Setup)"),
    183: ("install_printer", 0.90, "ServiceId 183 (Настройка/установка оргтехники и ПО)"),
    # 12 = Оргтехника и рабочие места
    12: ("printer_spooler_restart", 0.85, "ServiceId 12 (Workplaces & Peripherals Support)"),
    # 63 = Доступ к корпоративной сети Wi-Fi (WLAN-WORKNET)
    63: ("grant_wlan", 0.95, "ServiceId 63 (Corporate Wi-Fi WLAN-WORKNET)"),
    # 112 = Аппаратные неисправности рабочих мест / Выезд инженера
    112: ("offline_host", 0.90, "ServiceId 112 (Hardware/Workstation Availability)"),
    # 71 = Общие вопросы (сеть / доступность)
    71: ("offline_host", 0.80, "ServiceId 71 (General Network & Host Connectivity)"),
    # Directum / 1C / Ошибочные сервисы
    999: ("service_redirect", 0.95, "ServiceId 999 (Non-targeted Service / Redirect)"),
}


# Fuzzy keyword patterns in Service Name if ID is not statically known
SERVICE_NAME_PATTERNS = [
    (("принтер", "печать", "оргтехник", "мфу", "сканер"), "install_printer", 0.90, "Раздел каталога оргтехники"),
    (("wi-fi", "wifi", "вайфай", "беспроводн", "wlan"), "grant_wlan", 0.90, "Раздел каталога Wi-Fi"),
    (("увольнение", "блокировка учетной записи", "заблокировать", "оффбординг"), "account_lock", 0.90, "Раздел каталога блокировки доступа"),
    (("создать пользователя", "создание учетной записи", "новый сотрудник", "онбординг", "пользовател directum"), "account_create", 0.90, "Раздел каталога создания пользователей"),
    (("не включается", "нет питания", "не доступен пк", "оффлайн"), "offline_host", 0.85, "Раздел каталога неисправностей оборудования"),
    (("1с", "клининг", "бухгалтер", "хоз"), "service_redirect", 0.85, "Раздел внешних сервисов"),
]


class CatalogPriorProvider:
    """Evaluates a-priori scenario probabilities based on IntraService service catalog classification."""

    def get_priors(self, task: TaskDTO) -> Dict[str, Tuple[float, str]]:
        """Return scenario_key -> (prior_confidence, reason) mapping for all eligible candidate scenarios."""
        priors: Dict[str, Tuple[float, str]] = {}
        sid = task.service_id
        sname = (task.service_name or "").lower()

        # 1. Exact ServiceId clustering
        if sid is not None:
            if sid in SERVICE_IDS_PRINTER_SUPPORT:
                # All printing scenarios belong to this domain cluster
                priors["install_printer"] = (0.95, f"ServiceId {sid} (Оргтехника и печать)")
                priors["printer_spooler_restart"] = (0.90, f"ServiceId {sid} (Оргтехника и печать)")
                priors["default_printer_fix"] = (0.90, f"ServiceId {sid} (Оргтехника и печать)")
            elif sid in SERVICE_IDS_ACCOUNT_CREATE:
                priors["account_create"] = (0.95, f"ServiceId {sid} (Создание учетной записи / Доступ)")
            elif sid in SERVICE_IDS_ACCOUNT_LOCK:
                priors["account_lock"] = (0.95, f"ServiceId {sid} (Увольнение / Блокировка доступа)")
            elif sid in SERVICE_IDS_WLAN:
                priors["grant_wlan"] = (0.95, f"ServiceId {sid} (Корпоративный Wi-Fi)")
            elif sid in SERVICE_IDS_OFFLINE_HOST:
                priors["offline_host"] = (0.90, f"ServiceId {sid} (Доступность хоста / Аппаратный сбой)")
            elif sid in SERVICE_ID_MAPPINGS:
                key, conf, reason = SERVICE_ID_MAPPINGS[sid]
                priors[key] = (conf, reason)

        # 2. TaskTypeId 1018 (Directum User Account Provisioning)
        if task.task_type_id == 1018:
            priors["account_create"] = (0.95, "TaskTypeId 1018 (Заявка на пользователя Directum)")

        # 3. Fuzzy Service Name matching (if specific priors not already set)
        if sname:
            if any(kw in sname for kw in ("принтер", "печать", "оргтехник", "мфу", "сканер")):
                priors.setdefault("install_printer", (0.90, f"Раздел каталога оргтехники ('{task.service_name}')"))
                priors.setdefault("printer_spooler_restart", (0.90, f"Раздел каталога оргтехники ('{task.service_name}')"))
                priors.setdefault("default_printer_fix", (0.90, f"Раздел каталога оргтехники ('{task.service_name}')"))
            for keywords, key, conf, reason in SERVICE_NAME_PATTERNS:
                if any(kw in sname for kw in keywords):
                    priors.setdefault(key, (conf, f"{reason} ('{task.service_name}')"))

        return priors

    def get_prior(self, task: TaskDTO) -> Optional[Tuple[str, float, str]]:
        """Backwards-compatible helper returning single highest-confidence prior (for legacy code/tests)."""
        priors = self.get_priors(task)
        if not priors:
            return None
        # Return prioritized winner
        best_key = max(priors.keys(), key=lambda k: priors[k][0])
        conf, reason = priors[best_key]
        return best_key, conf, reason
