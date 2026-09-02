import asyncio
import contextlib
import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.config import settings
from app.routers.deps import get_operator_auth_b64, verify_admin_jwt
from app.services import crypto, intraservice
from app.services.deduplication import DuplicateDetector
from app.services.template_engine import auto_detect_template
from app.utils.normalizer import (
    extract_pc_names_from_text,
    is_valid_pc_name,
    normalize_pc_name,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class ApplyActionRequest(BaseModel):
    status_id: int
    comment: str
    minutes: int = 10
    executor_ids: str = "8664,10502"
    is_private: bool = False


class BulkApplyItem(BaseModel):
    task_id: int
    status_id: int
    comment: str
    minutes: int = 10
    executor_ids: str = "8664,10502"
    is_private: bool = False


class BulkApplyRequest(BaseModel):
    tasks: list[BulkApplyItem]


def _parse_task_custom_fields(
    data_xml: str | None, title: str = "", desc: str = ""
) -> dict[str, str]:
    res = {"pc_name": "", "phone": "", "room": "", "department": ""}
    if data_xml:
        matches = re.findall(r'<field id="(\d+)">([^<]*)</field>', data_xml)
        for fid, val in matches:
            v = val.strip()
            if not v:
                continue
            if fid in ("1089", "1112", "1203", "1120"):
                norm = normalize_pc_name(v)
                res["pc_name"] = (
                    norm if (norm and is_valid_pc_name(norm)) else v.upper()
                )
            elif fid in ("1088", "1202", "1075"):
                res["phone"] = v
            elif fid in ("1087", "1079"):
                res["room"] = v
            elif fid in ("1091", "1206", "1078"):
                res["department"] = v

    # Извлечение хостов из темы и описания, если хост не найден или для дополнительных хостов
    text_hosts = extract_pc_names_from_text(f"{title} {desc}")
    hosts = []
    if res["pc_name"]:
        hosts.append(res["pc_name"])
    for h in text_hosts:
        if h not in hosts:
            hosts.append(h)

    if not res["pc_name"] and hosts:
        res["pc_name"] = hosts[0]

    if len(hosts) > 1:
        res["pc_name"] = ", ".join(hosts)

    return res


async def _get_service_catalog_map() -> tuple[
    dict[int, dict], list[dict], dict[int, list[dict]]
]:
    """
    Возвращает:
    1. svc_map: словарь всех услуг по ID.
    2. root_services: список корневых сервисов (17 штук).
    3. subservices_by_root: словарь [root_id -> list of child services].
    """
    import app.routers.admin as admin

    r = admin.get_redis_client()
    catalog_str = await r.get("worker:service_catalog")
    if not catalog_str:
        with contextlib.suppress(Exception):
            from app.services.worker import sync_service_catalog

            await sync_service_catalog()
            catalog_str = await r.get("worker:service_catalog")

    if not catalog_str:
        return {}, [], {}

    try:
        flat_list = json.loads(catalog_str)
        svc_map = {s["id"]: s for s in flat_list if "id" in s}

        # 1. Выбираем корневые сервисы (ParentId отсутствует или равен None/0)
        root_services = []
        for s in flat_list:
            if not s.get("parent_id") or s.get("parent_id") not in svc_map:
                root_services.append({
                    "id": s["id"],
                    "name": s["name"],
                })

        # 2. Сопоставление дочерних подсервисов с корневыми разделами
        subservices_by_root: dict[int, list[dict]] = {
            r["id"]: [] for r in root_services
        }
        for s in flat_list:
            s_id = s["id"]
            curr = s
            visited = set()
            while (
                curr.get("parent_id")
                and curr.get("parent_id") in svc_map
                and curr.get("parent_id") not in visited
            ):
                visited.add(curr["id"])
                curr = svc_map[curr.get("parent_id")]
            root_id = curr.get("id")
            if root_id and root_id in subservices_by_root and s_id != root_id:
                subservices_by_root[root_id].append({
                    "id": s_id,
                    "name": s.get("name"),
                    "parent_id": s.get("parent_id"),
                })

        return svc_map, root_services, subservices_by_root
    except Exception as e:
        logger.error("Ошибка парсинга каталога услуг: %s", e)
        return {}, [], {}


def _resolve_service_hierarchy(
    service_id: int | None, svc_map: dict[int, dict]
) -> dict[str, Any]:
    """
    По ServiceId находит точное имя подуслуги, корневой сервис IntraService (1 из 17) и цепочку навигации.
    """
    if not service_id or service_id not in svc_map:
        return {
            "service_id": service_id,
            "service_name": "1-я линия технической поддержки",
            "root_service_id": None,
            "root_service_name": (
                "11. Общие вопросы" if not service_id else "Прочие сервисы"
            ),
            "service_path": "1-я линия технической поддержки",
        }

    curr = svc_map[service_id]
    leaf_name = curr.get("name") or "Не указана"
    path_names = [leaf_name]

    visited = set()
    while (
        curr.get("parent_id")
        and curr.get("parent_id") in svc_map
        and curr.get("parent_id") not in visited
    ):
        visited.add(curr["id"])
        curr = svc_map[curr.get("parent_id")]
        path_names.append(curr.get("name") or "")

    root_id = curr.get("id")
    root_name = curr.get("name") or leaf_name
    path_names.reverse()

    return {
        "service_id": service_id,
        "service_name": leaf_name,
        "root_service_id": root_id,
        "root_service_name": root_name,
        "service_path": " ➔ ".join(path_names),
    }


def _format_comment(
    template: str,
    pc_name: str = "",
    target_service: str = "",
    master_task_id: str = "",
) -> str:
    res = template or ""
    if "{pc_name}" in res:
        res = res.replace("{pc_name}", pc_name if pc_name else "вашем ПК")
    if "{target_service}" in res:
        res = res.replace(
            "{target_service}",
            target_service if target_service else "соответствующий раздел",
        )
    if "{master_task_id}" in res:
        res = res.replace("{master_task_id}", master_task_id)
    return res


def _classify_queue_task(
    task: dict[str, Any],
    svc_info: dict[str, Any] | None = None,
    pc_name: str = "",
) -> dict[str, Any]:
    decision = auto_detect_template(task)
    fallback_svc = (
        (svc_info.get("root_service_name") if svc_info else None)
        or task.get("ServiceName")
        or "1-я линия техподдержки"
    )

    t_key = decision.get("template_key", "general")
    rule_type = t_key
    if rule_type == "wifi_access":
        rule_type = "wlan_access"
    elif rule_type == "wrong_service":
        target_root = decision.get("target_root")
        if target_root == "06":
            rule_type = "redirect_1c"
        elif target_root == "05":
            rule_type = "redirect_directum"
        elif target_root == "08":
            rule_type = "redirect_security"
        elif target_root == "03":
            rule_type = "redirect_printers"
        else:
            rule_type = (
                f"redirect_{target_root}" if target_root else "wrong_service"
            )

    st_id = decision.get("status_id", 27)
    return {
        "rule_type": rule_type,
        "template_key": t_key,
        "category_label": decision.get("name", "1-я линия техподдержки"),
        "ai_summary": decision.get("name", "1-я линия техподдержки"),
        "target_service_name": decision.get("target_service_name")
        or fallback_svc,
        "is_redirect": decision.get("is_redirect", False) or st_id == 30,
        "has_ai_solution": st_id in (29, 30, 48),
        "score": 10 if (decision.get("is_redirect") or st_id in (29, 30)) else 7,
        "target_status_id": st_id,
        "target_status_name": decision.get("status_name", "В работе"),
        "suggested_comment": decision.get("comment", ""),
        "expenses": decision.get("expenses", 10),
        "badge_color": decision.get("badge_color", "secondary"),
    }


@router.get("/admin/api/queue", dependencies=[Depends(verify_admin_jwt)])
async def get_triage_queue(filter_id: int = 984, limit: int = 200):
    """
    Возвращает открытые заявки очереди 1-й линии с классификацией Rule Engine,
    кастомными полями, шаблонами, исполнителями и предложенными действиями.
    """
    import app.routers.admin as admin

    r = admin.get_redis_client()
    auth_encrypted = await r.get("worker:service_auth_b64")
    if not auth_encrypted:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервисный аккаунт IntraService не инициализирован. Выполните вход через панель администратора.",
        )

    auth_b64 = crypto.decrypt_token(auth_encrypted)
    params = {
        "filterid": str(filter_id or 984),
        "include": "executorids,status,customfields,service,comments,attachments",
        "pagesize": str(limit) if limit > 0 else "200",
        "page": "1",
    }

    raw_res = await intraservice.get_tasks(auth_b64=auth_b64, filters=params)
    tasks = []
    if isinstance(raw_res, dict):
        tasks = raw_res.get("Tasks", [])
    elif isinstance(raw_res, list):
        tasks = raw_res

    svc_map, root_services, subservices_by_root = (
        await admin._get_service_catalog_map()
    )

    # Детекция дубликатов
    detector = DuplicateDetector()
    duplicates = detector.find_duplicates(tasks)
    dup_map = {d["duplicate_task_id"]: d for d in duplicates}

    items = []
    for t in tasks:
        t_id = t.get("Id")
        if not t_id:
            continue
        c_fields = _parse_task_custom_fields(
            t.get("Data"),
            title=t.get("Name") or "",
            desc=t.get("Description") or "",
        )
        svc_info = _resolve_service_hierarchy(t.get("ServiceId"), svc_map)
        cls_info = _classify_queue_task(
            t, svc_info, pc_name=c_fields.get("pc_name", "")
        )
        has_ai = cls_info.get("has_ai_solution", False)

        # Формируем список вложений
        attachments = []
        raw_files = t.get("Attachments") or t.get("Files") or []
        if isinstance(raw_files, list):
            for f in raw_files:
                f_id = f.get("Id")
                attachments.append({
                    "id": f_id,
                    "name": f.get("Name") or f.get("FileName") or "Вложение",
                    "size": f.get("Size") or f.get("Length"),
                    "content_type": f.get("ContentType") or "",
                    "url": (
                        f.get("Url")
                        or f.get("DownloadUrl")
                        or f"/admin/api/attachments/{f_id}"
                    ),
                })

        is_dup = t_id in dup_map
        dup_info = dup_map.get(t_id)

        # Формируем строку исполнителей
        executors_val = (
            t.get("Executors")
            or t.get("Executor")
            or t.get("ExecutorName")
            or ""
        )
        if isinstance(executors_val, list):
            executors_str = ", ".join(
                str(e.get("Name") or e.get("Login") or e)
                for e in executors_val
            )
        elif isinstance(executors_val, dict):
            executors_str = (
                executors_val.get("Name") or executors_val.get("Login") or ""
            )
        else:
            executors_str = str(executors_val or "")

        executor_ids = t.get("ExecutorIds") or []
        if not executors_str and executor_ids:
            if isinstance(executor_ids, list):
                executors_str = ", ".join(str(x) for x in executor_ids)
            else:
                executors_str = str(executor_ids)

        items.append({
            "id": t_id,
            "name": t.get("Name") or "Без темы",
            "description": t.get("Description") or "",
            "ai_summary": cls_info.get("ai_summary", ""),
            "creator": (
                t.get("Creator") or t.get("CreatorLogin") or "Пользователь"
            ),
            "creator_login": t.get("CreatorLogin") or "",
            "created": t.get("Created") or "",
            "service_id": svc_info["service_id"],
            "service_name": svc_info["service_name"],
            "root_service_id": svc_info["root_service_id"],
            "root_service_name": svc_info["root_service_name"],
            "service_path": svc_info["service_path"],
            "target_service_name": (
                cls_info.get("target_service_name")
                or svc_info["root_service_name"]
            ),
            "is_redirect": cls_info.get("is_redirect", False),
            "has_ai_solution": has_ai,
            "status_id": t.get("StatusId"),
            "status_name": t.get("StatusName") or "Открыта",
            "pc_name": c_fields["pc_name"],
            "phone": c_fields["phone"],
            "room": c_fields["room"],
            "department": c_fields["department"],
            "rule_type": cls_info["rule_type"],
            "template_key": cls_info.get("template_key", "general"),
            "category_label": cls_info["category_label"],
            "score": cls_info["score"],
            "target_status_id": cls_info["target_status_id"],
            "target_status_name": cls_info["target_status_name"],
            "suggested_comment": cls_info["suggested_comment"],
            "original_comment": cls_info["suggested_comment"],
            "expenses": cls_info.get("expenses", 10),
            "is_private": False,
            "badge_color": cls_info["badge_color"],
            "has_attachments": len(attachments) > 0,
            "attachments": attachments,
            "is_duplicate": is_dup,
            "duplicate_info": dup_info,
            "executors": executors_str,
            "executor_ids": executor_ids,
        })

    return {
        "total": len(items),
        "filter_id": filter_id,
        "root_services": root_services,
        "subservices_by_root": subservices_by_root,
        "tasks": items,
        "duplicates": duplicates[:10],
    }


@router.get("/admin/api/tasks/{task_id}/open")
async def open_task_in_intraservice(task_id: int):
    """
    Перенаправляет браузер на веб-интерфейс IntraService для просмотра заявки.
    """
    base_url = settings.INTRASERVICE_URL.rstrip("/").replace("/api", "")
    target_url = f"{base_url}/Task/View/{task_id}"
    return RedirectResponse(
        url=target_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@router.get("/admin/api/tasks/{task_id}/details")
async def get_task_details(
    task_id: int,
    auth_b64: str = Depends(get_operator_auth_b64),
):
    """
    Возвращает расширенные детали задачи: историю комментариев, вложения, кастомные поля и права текущего оператора.
    """
    import app.routers.admin as admin

    task_data = await intraservice.get_single_task(
        auth_b64, task_id, include_rights=True
    )
    if not task_data:
        raise HTTPException(
            status_code=404, detail=f"Заявка #{task_id} не найдена"
        )

    lifetime_raw = await intraservice.get_task_lifetime(auth_b64, task_id)
    lifetime: list[dict[str, Any]] = []
    if isinstance(lifetime_raw, list):
        lifetime = [item for item in lifetime_raw if isinstance(item, dict)]
    elif isinstance(lifetime_raw, dict):
        for key in ("TaskLifeTimes", "LifeTimes", "Items", "Events"):
            val = lifetime_raw.get(key)
            if isinstance(val, list):
                lifetime = [item for item in val if isinstance(item, dict)]
                break

    # Формируем список комментариев
    comments = []
    for item in lifetime:
        if isinstance(item, dict) and item.get("Comment"):
            comments.append({
                "id": item.get("Id"),
                "author": (
                    item.get("UserName")
                    or item.get("UserLogin")
                    or "Пользователь"
                ),
                "created": item.get("Created"),
                "text": item.get("Comment"),
                "is_private": item.get("IsPrivateComment", False),
            })

    # Извлекаем вложения
    attachments = []
    raw_files = task_data.get("Attachments") or task_data.get("Files") or []
    if isinstance(raw_files, list):
        for f in raw_files:
            attachments.append({
                "id": f.get("Id"),
                "name": f.get("Name") or f.get("FileName") or "Вложение",
                "size": f.get("Size") or f.get("Length"),
                "content_type": f.get("ContentType") or "",
                "url": (
                    f.get("Url")
                    or f.get("DownloadUrl")
                    or f"/admin/api/attachments/{f.get('Id')}"
                ),
            })

    svc_map, _, _ = await admin._get_service_catalog_map()
    svc_info = _resolve_service_hierarchy(task_data.get("ServiceId"), svc_map)
    c_fields = _parse_task_custom_fields(
        task_data.get("Data"),
        title=task_data.get("Name") or "",
        desc=task_data.get("Description") or "",
    )
    cls_info = _classify_queue_task(
        task_data, svc_info, pc_name=c_fields.get("pc_name", "")
    )

    rights_data = task_data.get("Rights") or {}
    to_statuses = rights_data.get("ToStatuses") or []
    rights_info = {
        "to_statuses": to_statuses,
        "can_add_comment": bool(rights_data.get("CanAddComments", True)),
        "can_add_expenses": True,
    }

    return {
        "id": task_id,
        "name": task_data.get("Name") or "Без темы",
        "description": task_data.get("Description") or "",
        "ai_summary": cls_info.get("ai_summary", ""),
        "creator": (
            task_data.get("Creator")
            or task_data.get("CreatorLogin")
            or "Пользователь"
        ),
        "creator_login": task_data.get("CreatorLogin") or "",
        "created": task_data.get("Created") or "",
        "service_id": svc_info["service_id"],
        "service_name": svc_info["service_name"],
        "root_service_id": svc_info["root_service_id"],
        "root_service_name": svc_info["root_service_name"],
        "service_path": svc_info["service_path"],
        "status_id": task_data.get("StatusId"),
        "status_name": task_data.get("StatusName") or "",
        "pc_name": c_fields["pc_name"],
        "phone": c_fields["phone"],
        "room": c_fields["room"],
        "department": c_fields["department"],
        "comments": comments,
        "attachments": attachments,
        "cls_info": cls_info,
        "rights": rights_info,
    }


@router.get("/admin/api/tasks/{task_id}/attachments/{file_id}")
async def download_task_attachment(
    task_id: int,
    file_id: int,
    auth_b64: str = Depends(get_operator_auth_b64),
):
    """
    Скачивает бинарный файл вложения задачи из IntraService от имени авторизованного оператора.
    """
    content = await intraservice.download_attachment_file(
        auth_b64, task_id, file_id
    )
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Вложение #{file_id} не найдено или недоступно",
        )

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="attachment_{file_id}"'
        },
    )


@router.post("/admin/api/tasks/{task_id}/apply")
async def apply_task_action(
    task_id: int,
    payload: ApplyActionRequest,
    auth_b64: str = Depends(get_operator_auth_b64),
):
    """
    Интерактивно применяет действие к заявке (перевод в целевой статус, комментарий, трудозатраты)
    от имени текущего авторизованного оператора.
    """
    # 1. Pre-flight: проверяем статус в живой базе
    task_curr = await intraservice.get_single_task(
        auth_b64, task_id, include_rights=True
    )
    if task_curr:
        curr_status = task_curr.get("StatusId")
        if curr_status in (29, 30):
            return {
                "success": True,
                "already_closed": True,
                "task_id": task_id,
                "message": (
                    f"Заявка #{task_id} уже закрыта со статусом {curr_status}"
                ),
            }

    # 2. Списываем трудозатраты
    if payload.minutes > 0:
        await intraservice.add_task_expenses(
            auth_b64, task_id=task_id, minutes=payload.minutes
        )

    # 3. Переводим в целевой статус напрямую с комментарием
    ok = await intraservice.update_task_full(
        auth_b64,
        task_id=task_id,
        status_id=payload.status_id,
        comment=payload.comment.strip() if payload.comment else None,
        executor_ids=payload.executor_ids,
        is_private=payload.is_private,
    )

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Не удалось обновить статус заявки #{task_id} в IntraService. "
                "Проверьте права оператора на данный переход."
            ),
        )

    logger.info(
        "Заявка #%s успешно обновлена оператором в статус %s",
        task_id,
        payload.status_id,
    )

    # 4. Полуавтоматическое дообучение RAG: если заявка закрыта (29) и содержит содержательное решение
    if (
        payload.status_id == 29
        and payload.comment
        and len(payload.comment.strip()) >= 15
    ):
        try:
            from app.database.db import AsyncSessionLocal
            from app.services.rag import index_task_knowledge

            async def _bg_index():
                try:
                    async with AsyncSessionLocal() as session:
                        task_info = task_curr or await intraservice.get_single_task(
                            auth_b64, task_id
                        )
                        if task_info:
                            await index_task_knowledge(
                                db=session,
                                task_id=task_id,
                                original_name=(
                                    task_info.get("Name", "")
                                    or f"Заявка #{task_id}"
                                ),
                                problem=(
                                    task_info.get("Description", "")
                                    or task_info.get("Name", "")
                                ),
                                solution=payload.comment.strip(),
                                service_id=task_info.get("ServiceId") or 0,
                                service_name=task_info.get("ServiceName") or "",
                                status_name="Выполнена",
                            )
                            logger.info(
                                "Решение заявки #%s успешно проиндексировано в RAG",
                                task_id,
                            )
                except Exception as ex:
                    logger.debug(
                        "Ошибка фоновой индексации заявки #%s в RAG: %s",
                        task_id,
                        ex,
                    )

            asyncio.create_task(_bg_index())
        except Exception as e:
            logger.warning(
                "Не удалось запустить фоновую индексацию в RAG: %s", e
            )

    return {
        "success": True,
        "task_id": task_id,
        "final_status_id": payload.status_id,
        "message": f"Заявка #{task_id} переведена в статус {payload.status_id}",
    }


@router.post("/admin/api/tasks/bulk-apply")
async def bulk_apply_tasks(
    payload: BulkApplyRequest,
    auth_b64: str = Depends(get_operator_auth_b64),
):
    """
    Пакетно применяет действия к списку выбранных заявок от имени авторизованного оператора.
    """
    import app.routers.admin as admin

    applied = []
    failed = []

    for item in payload.tasks:
        try:
            req = ApplyActionRequest(
                status_id=item.status_id,
                comment=item.comment,
                minutes=item.minutes,
                executor_ids=item.executor_ids,
                is_private=item.is_private,
            )
            res = await admin.apply_task_action(
                item.task_id, req, auth_b64=auth_b64
            )
            applied.append({"task_id": item.task_id, "res": res})
        except Exception as e:
            logger.error(
                "Ошибка пакетного применения к задаче #%d: %s", item.task_id, e
            )
            failed.append({"task_id": item.task_id, "error": str(e)})

    return {
        "total": len(payload.tasks),
        "success_count": len(applied),
        "failed_count": len(failed),
        "applied": applied,
        "failed": failed,
    }
