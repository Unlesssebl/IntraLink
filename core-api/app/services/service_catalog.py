"""
Единый сервис каталога услуг IntraService (Single Source of Truth).
Обеспечивает построение иерархических путей сервисов, нормализацию названий,
трёхуровневое кэширование и отказоустойчивый fallback при отсутствии внешнего API.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from shared.json_utils import json_loads

logger = logging.getLogger("core_api.service_catalog")

# Справочник корневых сервисов каталога IntraService
ROOT_SERVICES: dict[str, dict[str, Any]] = {
    "01": {"id": 42, "name": "01. Учетные записи пользователей"},
    "02": {"id": 18, "name": "02. Установка и настройка программ"},
    "03": {"id": 19, "name": "03. Установка и обслуживание оргтехники"},
    "04": {"id": 20, "name": "04. Проблемы с сетью и интернетом"},
    "05": {"id": 23, "name": "05. DIRECTUM и тендерная площадка B2B"},
    "06": {"id": 15, "name": "06. Вопросы по 1С"},
    "07": {"id": 60, "name": "07. Вопросы по терминалу сбора данных"},
    "08": {"id": 72, "name": "08. Информационная безопасность"},
    "09": {"id": 24, "name": "09. Электронная цифровая подпись (ЭЦП)"},
    "10": {"id": 64, "name": "10. Телефония"},
    "11": {"id": 16, "name": "11. Общие вопросы"},
    "12": {"id": 125, "name": "12. Архив КТД"},
    "14": {"id": 136, "name": "14. MDC системы"},
    "15": {"id": 144, "name": "15. Вопросы по HelpDesk"},
    "16": {"id": 188, "name": "16. Вопросы по IPS PDM\\PLM"},
}

# Маппинг всех известных ID подразделов к их корневому разделу (01..16)
SERVICE_ID_TO_ROOT: dict[int, str] = {
    # 01. Учетные записи
    42: "01", 53: "01", 63: "01", 54: "01", 55: "01", 124: "01", 104: "01", 186: "01",
    # 02. Установка и настройка ПО
    18: "02", 58: "02", 59: "02", 149: "02",
    # 03. Оргтехника и ПК
    19: "03", 32: "03", 33: "03", 183: "03", 184: "03", 150: "03", 57: "03",
    # 04. Проблемы с сетью
    20: "04", 43: "04", 71: "04", 181: "04",
    # 05. Directum / B2B
    23: "05", 41: "05", 40: "05", 232: "05", 233: "05", 234: "05",
    # 06. Вопросы по 1С
    15: "06", 28: "06", 50: "06", 51: "06", 26: "06", 45: "06", 46: "06", 47: "06", 48: "06",
    52: "06", 27: "06", 29: "06", 39: "06", 185: "06",
    # 07. ТСД
    60: "07", 61: "07", 62: "07", 182: "07",
    # 08. ИБ
    72: "08", 56: "08", 74: "08", 73: "08", 38: "08", 36: "08", 105: "08", 128: "08", 130: "08", 129: "08", 75: "08", 219: "08",
    # 09. ЭЦП
    24: "09", 30: "09", 31: "09", 224: "09", 227: "09", 231: "09",
    # 10. Телефония
    64: "10", 65: "10", 66: "10", 67: "10", 68: "10", 69: "10", 70: "10",
    # 11. Общие вопросы
    16: "11", 106: "11",
    # 12. Архив КТД
    125: "12", 126: "12",
    # 14. MDC системы
    136: "14", 132: "14", 137: "14", 133: "14", 139: "14",
    # 15. HelpDesk
    144: "15", 151: "15", 127: "15",
    # 16. IPS PDM/PLM
    188: "16",
}


def get_root_number_for_service_id(service_id: int | None) -> str | None:
    """Возвращает номер корневого раздела ('01'..'16') для заданного ServiceId."""
    if service_id is None:
        return None
    return SERVICE_ID_TO_ROOT.get(int(service_id))


def get_root_name(root_num: str) -> str:
    """Возвращает человекочитаемое имя корневого раздела."""
    return ROOT_SERVICES.get(root_num, {}).get("name", f"Раздел {root_num}")


MAX_SERVICE_PATH_LENGTH = 120


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    """Нормализованная информация о сервисе и его месте в иерархии каталога."""
    id: int
    name: str
    parent_id: int | None
    path_ids: tuple[int, ...]
    path_ids_str: str
    service_path: str
    root_id: int | None
    root_num: str | None
    root_name: str | None

    @property
    def service_id(self) -> int:
        return self.id



class ServiceCatalogService:
    """
    Централизованный сервис разрешения путей и иерархии сервисов IntraService.
    Трёхуровневое разрешение: L1 (Memory) -> L2 (Redis) -> L3 (Static Catalog Fallback).
    """

    _l1_cache: dict[int, ServiceInfo] = {}
    _l1_cache_ts: float = 0.0
    _l1_ttl_seconds: float = 300.0  # 5 минут

    @classmethod
    def normalize_path_string(cls, parts: list[str]) -> str:
        """Собирает читаемую строку пути с ограничением длины."""
        cleaned_parts = [p.strip() for p in parts if p and p.strip()]
        if not cleaned_parts:
            return "Общие вопросы"

        full_path = " / ".join(cleaned_parts)
        if len(full_path) <= MAX_SERVICE_PATH_LENGTH:
            return full_path

        # Если путь слишком длинный: сохраняем корень и лист
        if len(cleaned_parts) > 2:
            short_path = f"{cleaned_parts[0]} / ... / {cleaned_parts[-1]}"
            if len(short_path) <= MAX_SERVICE_PATH_LENGTH:
                return short_path

        return full_path[: MAX_SERVICE_PATH_LENGTH - 3] + "..."

    @classmethod
    def build_catalog_map(cls, raw_services: list[dict[str, Any]]) -> dict[int, ServiceInfo]:
        """
        Строит полную карту сервисов ServiceInfo из сырого списка услуг IntraService.
        """
        if not raw_services:
            return {}

        raw_by_id: dict[int, dict[str, Any]] = {}
        for s in raw_services:
            if isinstance(s, dict):
                raw_id = s.get("Id") if s.get("Id") is not None else s.get("id")
                if raw_id is not None:
                    try:
                        raw_by_id[int(raw_id)] = s
                    except (ValueError, TypeError):
                        continue

        root_id_to_num = {v["id"]: k for k, v in ROOT_SERVICES.items()}
        result_map: dict[int, ServiceInfo] = {}

        for sid, s in raw_by_id.items():
            sname = str(s.get("Name") or s.get("name") or f"Сервис #{sid}").strip()
            parent_id_raw = s.get("ParentId") if s.get("ParentId") is not None else s.get("parent_id")
            parent_id = int(parent_id_raw) if parent_id_raw is not None and str(parent_id_raw).isdigit() else None

            # 1. Извлекаем цепочку идентификаторов
            path_ids: list[int] = []
            path_field = s.get("Path") or s.get("path")
            if isinstance(path_field, str) and path_field.strip():
                for chunk in path_field.strip().split("|"):
                    chunk_clean = chunk.strip()
                    if chunk_clean.isdigit():
                        path_ids.append(int(chunk_clean))

            # Если Path отсутствует, строим цепочку вверх по parent_id
            if not path_ids:
                curr_id: int | None = sid
                visited = set()
                chain = []
                while curr_id and curr_id not in visited:
                    visited.add(curr_id)
                    chain.append(curr_id)
                    parent_node = raw_by_id.get(curr_id)
                    p_id = parent_node.get("ParentId") if parent_node and parent_node.get("ParentId") is not None else (parent_node.get("parent_id") if parent_node else None)
                    curr_id = int(p_id) if p_id is not None and str(p_id).isdigit() else None
                path_ids = list(reversed(chain))

            if sid not in path_ids:
                path_ids.append(sid)

            path_ids_str = "|".join(str(i) for i in path_ids) + "|"

            # 2. Строим цепочку названий
            name_parts = []
            for ancestor_id in path_ids:
                node = raw_by_id.get(ancestor_id)
                node_name = str(node.get("Name") or node.get("name") or "").strip() if node else ""
                if node_name:
                    name_parts.append(node_name)
                elif ancestor_id == sid:
                    name_parts.append(sname)

            service_path = cls.normalize_path_string(name_parts)

            # 3. Вычисляем корень раздела
            root_num = get_root_number_for_service_id(sid)
            root_id: int | None = None
            if root_num and root_num in ROOT_SERVICES:
                root_id = ROOT_SERVICES[root_num]["id"]

            if not root_num and path_ids:
                for cand_id in path_ids:
                    cand_num = get_root_number_for_service_id(cand_id) or root_id_to_num.get(cand_id)
                    if cand_num:
                        root_num = cand_num
                        root_id = ROOT_SERVICES.get(cand_num, {}).get("id") or cand_id
                        break

            if not root_id and path_ids:
                root_id = path_ids[0]

            root_node = raw_by_id.get(root_id, {}) if root_id else {}
            root_name = ROOT_SERVICES.get(root_num, {}).get("name") if root_num else (
                root_node.get("Name") or root_node.get("name") or sname
            )

            result_map[sid] = ServiceInfo(
                id=sid,
                name=sname,
                parent_id=parent_id,
                path_ids=tuple(path_ids),
                path_ids_str=path_ids_str,
                service_path=service_path,
                root_id=root_id,
                root_num=root_num,
                root_name=root_name,
            )

        return result_map

    @classmethod
    async def get_all_services(cls, redis_client=None) -> dict[int, ServiceInfo]:
        """
        Возвращает полную карту сервисов.
        L1 (Memory) -> L2 (Redis) -> L3 (Static Catalog).
        """
        now = time.monotonic()
        if cls._l1_cache and (now - cls._l1_cache_ts < cls._l1_ttl_seconds):
            return cls._l1_cache

        # 1. Попытка чтения из Redis
        if redis_client is None:
            try:
                from app.services.worker import get_redis_client
                redis_client = get_redis_client()
            except Exception:
                pass

        if redis_client is not None:
            try:
                raw_catalog = await redis_client.get("worker:service_catalog")
                if raw_catalog:
                    catalog_data = json_loads(raw_catalog)
                    if isinstance(catalog_data, list):
                        built_map = cls.build_catalog_map(catalog_data)
                        if built_map:
                            cls._l1_cache = built_map
                            cls._l1_cache_ts = now
                            return cls._l1_cache
            except Exception as e:
                logger.debug("Не удалось прочитать worker:service_catalog из Redis: %s", e)

        # 2. Если L1 пуст, используем статический fallback
        fallback_map = cls._build_static_fallback_map()
        if not cls._l1_cache:
            cls._l1_cache = fallback_map
            cls._l1_cache_ts = now
        return cls._l1_cache

    @classmethod
    def _build_static_fallback_map(cls) -> dict[int, ServiceInfo]:
        """Строит гарантированный статический справочник на базе ROOT_SERVICES и SERVICE_ID_TO_ROOT."""
        fallback: dict[int, ServiceInfo] = {}

        # 1. Корневые сервисы
        for r_num, r_info in ROOT_SERVICES.items():
            rid = r_info["id"]
            rname = r_info["name"]
            fallback[rid] = ServiceInfo(
                id=rid,
                name=rname,
                parent_id=None,
                path_ids=(rid,),
                path_ids_str=f"{rid}|",
                service_path=rname,
                root_id=rid,
                root_num=r_num,
                root_name=rname,
            )

        # 2. Дочерние известные сервисы
        for sid, r_num in SERVICE_ID_TO_ROOT.items():
            if sid in fallback:
                continue
            r_info = ROOT_SERVICES.get(r_num)
            r_id = r_info["id"] if r_info else None
            r_name = r_info["name"] if r_info else f"Раздел {r_num}"
            path_ids = (r_id, sid) if r_id else (sid,)
            path_str = f"{r_id}|{sid}|" if r_id else f"{sid}|"
            service_path = f"{r_name} / Подраздел #{sid}"

            fallback[sid] = ServiceInfo(
                id=sid,
                name=f"Подраздел #{sid}",
                parent_id=r_id,
                path_ids=path_ids,
                path_ids_str=path_str,
                service_path=service_path,
                root_id=r_id,
                root_num=r_num,
                root_name=r_name,
            )

        return fallback

    @classmethod
    async def get_service_info(
        cls,
        service_id: int | None,
        service_name: str | None = None,
        redis_client=None,
    ) -> ServiceInfo | None:
        """
        Возвращает ServiceInfo для заданного service_id с трёхуровневым fallback.
        Никогда не падает и всегда возвращает валидный объект, если передан service_id.
        """
        if service_id is None:
            return None

        try:
            sid = int(service_id)
        except (ValueError, TypeError):
            return None

        catalog = await cls.get_all_services(redis_client=redis_client)
        info = catalog.get(sid)
        if info:
            if "Подраздел #" in info.service_path and service_name and "Подраздел" not in service_name:
                root_name = info.root_name or (get_root_name(info.root_num) if info.root_num else None)
                new_path = f"{root_name} / {service_name}" if root_name and root_name != service_name else service_name
                return ServiceInfo(
                    id=info.id,
                    name=service_name,
                    parent_id=info.parent_id,
                    path_ids=info.path_ids,
                    path_ids_str=info.path_ids_str,
                    service_path=cls.normalize_path_string([new_path]),
                    root_id=info.root_id,
                    root_num=info.root_num,
                    root_name=info.root_name,
                )
            return info

        # Fallback для нового или неизвестного сервиса
        root_num = get_root_number_for_service_id(sid)
        root_name = get_root_name(root_num) if root_num else None
        sname = service_name or (f"Сервис #{sid}")

        if root_name and root_name != sname:
            service_path = f"{root_name} / {sname}"
        else:
            service_path = sname


        return ServiceInfo(
            id=sid,
            name=sname,
            parent_id=None,
            path_ids=(sid,),
            path_ids_str=f"{sid}|",
            service_path=cls.normalize_path_string([service_path]),
            root_id=None,
            root_num=root_num,
            root_name=root_name,
        )

    @classmethod
    async def get_service_path(
        cls,
        service_id: int | None,
        fallback_name: str | None = None,
        redis_client=None,
    ) -> tuple[str, list[int]]:
        """
        Удобный метод-хелпер для RAG:
        Возвращает кортеж (service_path, list[int] path_ids).
        """
        if not service_id:
            name = fallback_name or "Общие вопросы"
            return name, []
        info = await cls.get_service_info(service_id, service_name=fallback_name, redis_client=redis_client)
        if not info:
            name = fallback_name or f"Сервис #{service_id}"
            return name, [int(service_id)]
        return info.service_path, list(info.path_ids)

    @classmethod
    async def resolve_service_path(
        cls,
        service_id: int | None,
        fallback_name: str | None = None,
        redis_client=None,
    ) -> str:
        """Возвращает готовую строку полного пути сервиса."""
        path_str, _ = await cls.get_service_path(service_id, fallback_name=fallback_name, redis_client=redis_client)
        return path_str

    @classmethod
    async def get_leaf_services(
        cls,
        redis_client=None,
        raw_services: list[dict] | None = None,
    ) -> list[ServiceInfo]:
        """
        Возвращает список всех конечных сервисов (листьев каталога), в которые создаются заявки.
        Листом считается сервис, на который не ссылается ни один дочерний элемент.
        """
        if raw_services is not None:
            all_services = cls.build_catalog_map(raw_services)
        else:
            all_services = await cls.get_all_services(redis_client=redis_client)

        parent_ids = {s.parent_id for s in all_services.values() if s.parent_id is not None}
        leaves = [s for s in all_services.values() if s.id not in parent_ids]
        # Сортируем по номеру корневого раздела и ID для предсказуемости
        return sorted(leaves, key=lambda s: (s.root_num or "99", s.id))

    @classmethod
    async def get_leaf_services_by_root(
        cls,
        root: str | int,
        redis_client=None,
        raw_services: list[dict] | None = None,
    ) -> list[ServiceInfo]:
        """Возвращает конечные сервисы для конкретного корневого раздела (по номеру или ID)."""
        leaves = await cls.get_leaf_services(redis_client=redis_client, raw_services=raw_services)
        root_str = str(root).strip()
        # Если передан номер с лидирующим нулем или без него (например 1 или "01")
        if root_str.isdigit():
            norm_num = f"{int(root_str):02d}"
            root_id_int = int(root_str)
        else:
            norm_num = root_str
            root_id_int = -1

        return [
            s for s in leaves
            if s.root_num == norm_num or s.root_id == root_id_int or (s.root_num and s.root_num.lstrip("0") == root_str.lstrip("0"))
        ]

