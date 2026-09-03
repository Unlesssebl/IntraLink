"""
Доменный сервис триажа, оркестрации пачек заявок, рекомендаций и применения решений.
Инкапсулирует бизнес-логику для предотвращения разрастания роутеров (SRP).
"""

import asyncio
import logging
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services import intraservice
from app.services.ai import RoutingMetadata, data_sanitizer
from app.services.deduplication import DuplicateDetector
from app.services.rules.catalog import (
    get_root_number_for_service_id,
)

logger = logging.getLogger("core_api.services.triage_service")


class TriageService:
    """Сервис бизнес-логики и оркестрации триажа заявок Helpdesk."""

    @staticmethod
    async def prepare_triage_batch(
        service_auth_b64: str,
        db: AsyncSession,
        filter_id: int = 984,
        limit: int = 5,
        page: int = 1,
        service_prefix: str | None = None,
        redirect_only: bool = False,
        include_skipped: bool = False,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Возвращает подготовленную пачку заявок с авто-подбором шаблонов Rule Engine,
        детекцией дубликатов, семантическим RAG контекстом и телеметрией 0ms.
        """
        import app.routers.triage as tr

        fetch_limit = max(limit * 4, 40)
        tasks = await intraservice.get_tasks_by_filter(
            auth_b64=service_auth_b64,
            filter_id=filter_id,
            page=1,
            page_size=fetch_limit,
        )

        if not tasks:
            return {
                "total_open": 0,
                "filter_id": filter_id,
                "page": page,
                "tasks": [],
                "duplicates": [],
            }

        # Исключаем закрытые (29, 30) и пропущенные в смене
        skipped_ids = (
            set()
            if include_skipped
            else await tr.get_skipped_task_ids(operator_id)
        )
        active_tasks = [
            t
            for t in tasks
            if t.get("Id") not in skipped_ids and t.get("StatusId") not in (29, 30)
        ]

        # Детекция дубликатов
        detector = DuplicateDetector()
        all_duplicates = detector.find_duplicates(active_tasks)
        dup_map = {d["duplicate_task_id"]: d for d in all_duplicates}

        # Фильтрация по разделу каталога
        if service_prefix:
            p_clean = (
                service_prefix.strip().zfill(2)
                if service_prefix.strip().isdigit() and len(service_prefix.strip()) == 1
                else service_prefix.strip()
            )
            filtered = []
            for t in active_tasks:
                s_id = t.get("ServiceId")
                root_num = get_root_number_for_service_id(s_id)
                s_name = (t.get("ServiceName") or "").lower()
                if root_num and root_num == p_clean:
                    filtered.append(t)
                elif p_clean.lower() in s_name:
                    filtered.append(t)
            active_tasks = filtered

        # Фильтрация только редиректов
        if redirect_only:
            active_tasks = [t for t in active_tasks if tr.detect_service_redirect(t)]

        # Пагинация
        start_idx = (page - 1) * limit
        page_tasks = active_tasks[start_idx : start_idx + limit]

        result_items = []
        for t in page_tasks:
            t_id = t.get("Id")
            t_name = t.get("Name") or ""
            t_desc = t.get("Description") or ""
            query_text = f"{t_name}. {t_desc}".strip()

            # Семантический RAG-поиск
            kb_matches = await tr.search_knowledge_base(
                db=db, query_text=query_text, limit=2, distance_threshold=0.70
            )

            # Rule Engine рекомендация
            decision = tr.auto_detect_template(
                task=t,
                kb_matches=kb_matches,
                redirect_mode=redirect_only,
            )

            # Zero Trust DLP оценка контура
            circuit_dec = data_sanitizer.evaluate_circuit(
                prompt=query_text,
                metadata=RoutingMetadata(service_id=t.get("ServiceId")),
            )

            is_dup = t_id in dup_map
            dup_info = dup_map.get(t_id)

            # Экспресс-телеметрия хоста (0ms из кэша Redis)
            telemetry = await tr.get_task_telemetry(t_id)
            if telemetry is None:
                asyncio.create_task(tr.prefetch_task_telemetry(t))

            meta = t.get("_field_meta") or {}
            pc_name = meta.get("pc_name") or t.get("pc_name") or ""
            if not pc_name and t.get("Data"):
                from shared.normalizer import extract_pc_names_from_text
                pcs = extract_pc_names_from_text(t["Data"])
                if pcs:
                    pc_name = pcs[0]
            result_items.append({
                "task": t,
                "task_id": t_id,
                "name": t.get("Name"),
                "created": t.get("Created"),
                "status_id": t.get("StatusId"),
                "status_name": t.get("StatusName"),
                "service_id": t.get("ServiceId"),
                "service_name": t.get("ServiceName"),
                "creator": t.get("Creator"),
                "creator_phone": meta.get("phone") or t.get("CreatorPhone") or "—",
                "pc_name": pc_name,
                "room": meta.get("room") or "",
                "has_attachments": t.get("_has_attachments", False),
                "attachments_count": len(t.get("_attachments_list", [])),
                "suggested_action": decision,
                "is_duplicate": is_dup,
                "duplicate_info": dup_info,
                "kb_matches": kb_matches,
                "telemetry": telemetry,
                "circuit": circuit_dec.circuit.value,
                "circuit_reason": circuit_dec.reason,
                "requires_sanitization": circuit_dec.requires_sanitization,
            })

        return {
            "total_open": len(active_tasks),
            "filter_id": filter_id,
            "page": page,
            "tasks": result_items,
            "duplicates": all_duplicates[:10],
        }

    @staticmethod
    async def get_task_card_details(
        service_auth_b64: str,
        db: AsyncSession,
        task_id: int,
    ) -> dict[str, Any] | None:
        """
        Возвращает расширенную карточку задачи с нормализацией, историей переписки,
        RAG-совпадениями, телеметрией и AI-синтезом решения.
        """
        import app.routers.triage as tr

        task = await intraservice.get_single_task(service_auth_b64, task_id)
        if not task:
            return None

        history = await intraservice.get_task_lifetime(service_auth_b64, task_id) or []

        telemetry = await tr.get_task_telemetry(task_id)
        if telemetry is None:
            telemetry = await tr.prefetch_task_telemetry(task)

        t_name = task.get("Name") or ""
        t_desc = task.get("Description") or ""
        query_text = f"{t_name}. {t_desc}".strip()

        kb_matches = await tr.search_knowledge_base(
            db=db, query_text=query_text, limit=3, distance_threshold=0.70
        )

        decision = tr.auto_detect_template(task=task, kb_matches=kb_matches)

        circuit_dec = data_sanitizer.evaluate_circuit(
            prompt=query_text,
            metadata=RoutingMetadata(service_id=task.get("ServiceId")),
        )

        ai_resolution = await tr.synthesize_triage_resolution(
            task=task,
            kb_matches=kb_matches,
            telemetry=telemetry,
            circuit=circuit_dec.circuit,
        )

        return {
            "task": task,
            "history": history,
            "kb_matches": kb_matches,
            "telemetry": telemetry,
            "suggested_action": decision,
            "ai_suggested_resolution": ai_resolution,
            "circuit": circuit_dec.circuit.value,
            "circuit_reason": circuit_dec.reason,
            "requires_sanitization": circuit_dec.requires_sanitization,
        }

    @staticmethod
    async def apply_triage_resolution(
        service_auth_b64: str,
        db: AsyncSession,
        task_ids: list[int],
        status_id: int,
        comment: str = "",
        expenses: int = 0,
        executor_ids: str | None = None,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Применяет решение к заявке/группе заявок:
        1. Перевод в статус 27 (В работе) при необходимости.
        2. Перевод в целевой статус (29, 30, 35, 48) с комментарием.
        3. Списание трудозатрат.
        4. Автообучение pgvector RAG при закрытии.
        """
        import app.routers.triage as tr

        exec_ids = executor_ids or settings.DEFAULT_EXECUTOR_IDS
        results = []

        for tid in task_ids:
            if dry_run:
                results.append({
                    "task_id": tid,
                    "status": "simulated",
                    "target_status_id": status_id,
                })
                continue

            # 1. При необходимости берем в работу (27)
            if status_id != 27:
                await intraservice.update_task_full(
                    auth_b64=service_auth_b64,
                    task_id=tid,
                    status_id=27,
                    executor_ids=exec_ids,
                )

            # 2. Обновление в целевой статус
            upd_ok = await intraservice.update_task_full(
                auth_b64=service_auth_b64,
                task_id=tid,
                status_id=status_id,
                comment=comment if comment else None,
                executor_ids=exec_ids,
            )

            # 3. Списание трудозатрат
            exp_ok = True
            if expenses and expenses > 0:
                exp_ok = await intraservice.add_task_expenses(
                    auth_b64=service_auth_b64,
                    task_id=tid,
                    minutes=expenses,
                    user_id=settings.PRIMARY_EXECUTOR_ID,
                )

            # 4. Авто-индексация RAG
            clean_comment = comment.strip() if comment else ""
            should_index = False
            if status_id == 29 and clean_comment:
                should_index = True
            elif status_id == 30 and len(clean_comment) >= 35:
                if not clean_comment.startswith("Заявка переведена в статус Отменена"):
                    should_index = True

            if should_index:
                try:
                    task_data = await intraservice.get_single_task(service_auth_b64, tid)
                    if task_data:
                        t_name = task_data.get("Name") or f"Заявка #{tid}"
                        t_desc = task_data.get("Description") or ""
                        s_id = task_data.get("ServiceId") or 0
                        s_name = task_data.get("ServiceName") or "Общие"
                        st_name = "Выполнена" if status_id == 29 else "Отменена"

                        await tr.index_task_knowledge(
                            db=db,
                            task_id=tid,
                            original_name=t_name,
                            problem=f"{t_name}. {t_desc}".strip(),
                            solution=clean_comment,
                            service_id=s_id,
                            service_name=s_name,
                            status_name=st_name,
                            classification_data={
                                "type": "auto_indexed_by_triage",
                                "status_id": status_id,
                            },
                        )
                except Exception as e:
                    logger.error("Ошибка автоиндексации заявки #%d в RAG: %s", tid, e)

            results.append({
                "task_id": tid,
                "status": "success" if (upd_ok and exp_ok) else "partial_failure",
                "update_ok": upd_ok,
                "expenses_ok": exp_ok,
            })

        return results

    @staticmethod
    async def find_queue_duplicates(
        service_auth_b64: str,
        filter_id: int = 984,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Поиск и группировка заявок-дубликатов в очереди."""
        tasks = await intraservice.get_tasks_by_filter(
            auth_b64=service_auth_b64,
            filter_id=filter_id,
            page=1,
            page_size=max(limit * 5, 50),
        )
        active_tasks = [t for t in tasks if t.get("StatusId") not in (29, 30)]
        detector = DuplicateDetector()
        duplicates = detector.find_duplicates(active_tasks)
        return duplicates[:limit]
