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

from app.services.rules.catalog import ROOT_SERVICES, SERVICE_ID_TO_ROOT, get_root_name, get_root_number_for_service_id
from shared.json_utils import json_loads

logger = logging.getLogger("core_api.service_catalog")

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
            if isinstance(s, dict) and s.get("Id"):
                try:
                    raw_by_id[int(s["Id"])] = s
                except (ValueError, TypeError):
                    continue

        root_id_to_num = {v["id"]: k for k, v in ROOT_SERVICES.items()}
        result_map: dict[int, ServiceInfo] = {}

        for sid, s in raw_by_id.items():
            sname = str(s.get("Name") or f"Сервис #{sid}").strip()
            parent_id_raw = s.get("ParentId")
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
                    p_id = parent_node.get("ParentId") if parent_node else None
                    curr_id = int(p_id) if p_id is not None and str(p_id).isdigit() else None
                path_ids = list(reversed(chain))

            if sid not in path_ids:
                path_ids.append(sid)

            path_ids_str = "|".join(str(i) for i in path_ids) + "|"

            # 2. Строим цепочку названий
            name_parts = []
            for ancestor_id in path_ids:
                node = raw_by_id.get(ancestor_id)
                node_name = str(node.get("Name") or "").strip() if node else ""
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

            root_name = ROOT_SERVICES.get(root_num, {}).get("name") if root_num else (
                raw_by_id.get(root_id, {}).get("Name") if root_id else sname
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
