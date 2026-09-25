"""Catalog-First Prior Provider for IntraLink v2 Autopilot.

Maps structured ticket attributes (ServiceId, ServiceName, TaskTypeId, TaskTypeName)
to an a-priori scenario hypothesis with high initial confidence (P0 >= 0.90).
"""

import logging
from typing import Dict, Optional, Tuple

from core.intraservice.dto import TaskDTO

logger = logging.getLogger("core.scenarios.catalog_prior")

# Known exact service ID mappings
SERVICE_ID_MAPPINGS: Dict[int, Tuple[str, float, str]] = {
    # 55 = Заявка на пользователя Directum
    55: ("account_create", 0.95, "ServiceId 55 (Directum User Account Provisioning)"),
    # 232 = Доступ к корпоративным системам
    232: ("account_create", 0.95, "ServiceId 232 (Corporate Systems Access Provisioning)"),
    # 8 = Увольнение / блокировка доступа
    8: ("account_lock", 0.95, "ServiceId 8 (Employee Offboarding / Account Lock)"),
    # 62 = Установка локальных и сетевых принтеров / МФУ
    62: ("install_printer", 0.95, "ServiceId 62 (Printers/MFU installation)"),
    # 63 = Доступ к корпоративной сети Wi-Fi (WLAN-WORKNET)
    63: ("grant_wlan", 0.95, "ServiceId 63 (Corporate Wi-Fi WLAN-WORKNET)"),
    # 112 = Аппаратные неисправности рабочих мест / Выезд инженера
    112: ("offline_host", 0.90, "ServiceId 112 (Hardware/Workstation Availability)"),
    # Directum / 1C / Ошибочные сервисы
    999: ("service_redirect", 0.95, "ServiceId 999 (Non-targeted Service / Redirect)"),
}

# Fuzzy keyword patterns in Service Name if ID is not statically known
SERVICE_NAME_PATTERNS = [
    (("создать пользователя", "создание учетной записи", "новый сотрудник", "онбординг", "пользовател directum"), "account_create", 0.90, "Service relates to user onboarding"),
    (("увольнение", "блокировка учетной записи", "заблокировать пользователя", "оффбординг"), "account_lock", 0.90, "Service relates to employee offboarding"),
    (("очередь печати", "spooler", "спулер"), "printer_spooler_restart", 0.90, "Service name relates to print queue/spooler"),
    (("по умолчанию", "дефолтн"), "default_printer_fix", 0.90, "Service name relates to default printer setup"),
    (("принтер", "печать", "мфу", "сканер"), "install_printer", 0.90, "Service name relates to printing/MFU"),
    (("wi-fi", "wifi", "вайфай", "беспроводн", "wlan"), "grant_wlan", 0.90, "Service name relates to Wi-Fi/WLAN"),
    (("не включается", "нет питания", "не доступен пк", "оффлайн"), "offline_host", 0.85, "Service name relates to workstation power/hardware"),
    (("1с", "клининг", "бухгалтер", "хоз"), "service_redirect", 0.85, "Service name relates to redirectable external services"),
]


class CatalogPriorProvider:
    """Evaluates a-priori scenario probability based on IntraService service catalog classification."""

    def get_prior(self, task: TaskDTO) -> Optional[Tuple[str, float, str]]:
        """Return (scenario_key, prior_confidence, reason) if catalog matches, or None.

        Weight: 0.5 contribution in multi-factor scoring.
        """
        # 1. Exact ServiceId match
        if task.service_id is not None and task.service_id in SERVICE_ID_MAPPINGS:
            key, conf, reason = SERVICE_ID_MAPPINGS[task.service_id]
            logger.debug("Catalog exact match for ticket #%s: %s (conf: %.2f)", task.id, key, conf)
            return key, conf, reason

        # 1.5. TaskTypeId 1018 (Directum User Account Provisioning)
        if task.task_type_id is not None and task.task_type_id == 1018:
            logger.debug("Catalog task_type exact match for ticket #%s: account_create", task.id)
            return "account_create", 0.95, "TaskTypeId 1018 (Directum User Account)"

        # 2. Service Name matching
        if task.service_name:
            norm_service_name = task.service_name.lower()
            for keywords, key, conf, reason in SERVICE_NAME_PATTERNS:
                if any(kw in norm_service_name for kw in keywords):
                    logger.debug("Catalog name match for ticket #%s: %s via '%s'", task.id, key, norm_service_name)
                    return key, conf, f"{reason} ('{task.service_name}')"

        return None
