"""
Модуль обратной совместимости для старых импортов каталога.
Все актуальные определения перенесены в app.services.service_catalog.
"""

from app.services.service_catalog import (
    ROOT_SERVICES,
    SERVICE_ID_TO_ROOT,
    get_root_name,
    get_root_number_for_service_id,
)

__all__ = [
    "ROOT_SERVICES",
    "SERVICE_ID_TO_ROOT",
    "get_root_name",
    "get_root_number_for_service_id",
]
