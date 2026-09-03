"""
Доменный сервис триажа, оркестрации пачек заявок, рекомендаций и применения решений.
Инкапсулирует бизнес-логику для предотвращения разрастания роутеров (SRP).
"""

import asyncio
import hashlib
import inspect
import json
import logging
import time
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services import intraservice
from app.services.ai import RoutingMetadata, data_sanitizer
from app.services.ai_synthesis import calculate_confidence_score
from app.services.deduplication import DuplicateDetector
from app.services.rules.credentials import CredentialsRule
from app.services.rules.catalog import (
    ROOT_SERVICES,
    get_root_number_for_service_id,
)

logger = logging.getLogger("core_api.services.triage_service")


class TriageService:
    """Сервис бизнес-логики и оркестрации триажа заявок Helpdesk."""

    _catalog_cache: dict[int, dict[str, Any]] = {}
    _catalog_cache_ts: float = 0.0
    _EXECUTION_RULE_ACTIONS = {
        "wlan_access": {"grant_wlan", "wifi"},
        "user_creation": {"create_user"},
    }

    @staticmethod
    def _telemetry_to_rule_diag(
        telemetry: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Адаптирует каноническую телеметрию к контракту диагностических правил."""
        if not telemetry:
            return None

        status = str(telemetry.get("status") or "").upper()
        is_online = status == "ONLINE" or bool(
            telemetry.get("ping_ok")
            or telemetry.get("winrm_port_5985")
            or telemetry.get("smb_port_445")
        )
        return {
            **telemetry,
            "target": telemetry.get("canonical_name")
            or telemetry.get("pc_name")
            or "UNKNOWN",
            "is_online": is_online,
        }

    @classmethod
    async def _validate_execution_proof(
        cls,
        job_id: str | None,
        task_id: int,
        expected_actions: set[str],
    ) -> tuple[bool, str | None]:
        if not job_id:
            return (
                False,
                "Для статуса «Выполнена» требуется подтверждение успешной команды Execution Worker.",
            )
        try:
            import app.routers.triage as tr

            raw = await tr.get_redis_client().get(f"execution_job:{job_id}")
            if not raw:
                return False, f"Команда исполнения '{job_id}' не найдена."
            data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
        except Exception as exc:
            logger.warning("Не удалось проверить execution proof %s: %s", job_id, exc)
            return False, "Не удалось проверить результат команды исполнения."

        proof_task_id = data.get("task_id")
        try:
            proof_task_id = int(proof_task_id)
        except (TypeError, ValueError):
            proof_task_id = 0
        proof_action = str(data.get("action") or data.get("command_type") or "")

        if data.get("status") != "success":
            return False, f"Команда '{job_id}' не завершена успешно."
        if proof_task_id != task_id:
            return False, f"Команда '{job_id}' относится к другой заявке."
        if proof_action not in expected_actions:
            return False, f"Команда '{job_id}' не подтверждает требуемое действие."
        return True, None

    @classmethod
    async def get_service_catalog_map(cls, service_auth_b64: str) -> dict[int, dict[str, Any]]:
        """Кэширует справочник сервисов IntraService (ID -> {Name, RootNum, RootName, RootId})."""
        now = time.monotonic()
        if cls._catalog_cache and (now - cls._catalog_cache_ts < 300.0):
            return cls._catalog_cache
        try:
            raw_services = await intraservice.get_services(service_auth_b64) or []
            if isinstance(raw_services, dict):
                services = raw_services.get("Services") or []
            elif isinstance(raw_services, list):
                services = raw_services
            else:
                services = []

            root_id_to_num = {v["id"]: k for k, v in ROOT_SERVICES.items()}

            cat_map: dict[int, dict[str, Any]] = {}
            for s in services:
                if not isinstance(s, dict):
                    continue
                sid = s.get("Id")
                sname = s.get("Name")
                parent_id = s.get("ParentId")
                if sid:
                    root_num = get_root_number_for_service_id(sid)
                    if not root_num and parent_id:
                        root_num = get_root_number_for_service_id(parent_id) or root_id_to_num.get(parent_id)

                    root_info = ROOT_SERVICES.get(root_num) if root_num else None
                    if not root_info and parent_id and parent_id in root_id_to_num:
                        root_num = root_id_to_num[parent_id]
                        root_info = ROOT_SERVICES.get(root_num)

                    cat_map[sid] = {
                        "id": sid,
                        "name": sname,
                        "parent_id": parent_id,
                        "root_num": root_num,
                        "root_id": root_info.get("id") if root_info else (parent_id or sid),
                        "root_name": root_info.get("name") if root_info else (sname if not parent_id else "Общие вопросы"),
                    }
            if cat_map:
                cls._catalog_cache = cat_map
                cls._catalog_cache_ts = now
            return cls._catalog_cache
        except Exception as e:
            logger.warning("Ошибка получения справочника сервисов IntraService: %s", e)
            return cls._catalog_cache or {}

    @classmethod
    async def prepare_triage_batch(
        cls,
        service_auth_b64: str,
        db: AsyncSession,
        filter_id: int = 984,
        limit: int = 5,
        page: int = 1,
        service_prefix: str | None = None,
        redirect_only: bool = False,
        include_skipped: bool = False,
        include_rag: bool = False,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Возвращает подготовленную пачку заявок с авто-подбором шаблонов Rule Engine,
        детекцией дубликатов, семантическим RAG контекстом и телеметрией 0ms.
        """
        import app.routers.triage as tr

        # IntraService не возвращает total после нормализации ответа, поэтому
        # забираем максимально допустимую страницу. Иначе page > 8 при
        # стандартном limit=5 ложно выглядела пустой, а дедупликация работала
        # только на первых 40 заявках.
        fetch_limit = 500
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

        catalog_map = await cls.get_service_catalog_map(service_auth_b64)

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
                s_name = (t.get("ServiceName") or catalog_map.get(s_id, {}).get("name") or "").lower()
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

            routing_metadata = RoutingMetadata(service_id=t.get("ServiceId"))
            circuit_dec = data_sanitizer.evaluate_circuit(
                prompt=query_text,
                metadata=routing_metadata,
            )

            # Решение использует уже закэшированную телеметрию. Если кэша нет,
            # запускаем prefetch; результат войдет в решение при следующем чтении.
            telemetry = await tr.get_task_telemetry(t_id)
            if telemetry is None:
                asyncio.create_task(tr.prefetch_task_telemetry(t))
            rule_diag = cls._telemetry_to_rule_diag(telemetry)

            is_dup = t_id in dup_map
            dup_info = dup_map.get(t_id)

            kb_matches = []
            decision = None

            # 1. Приоритет #1: Дубликат (Транзакционный дедупликатор БД)
            if is_dup:
                master_id = (dup_info or {}).get("master_task_id", "")
                decision = {
                    "template_key": "duplicate_task",
                    "name": f"Отмена дубликата (привязка к #{master_id})" if master_id else "Отмена дубликата",
                    "status_id": 30,
                    "status_name": "Отменена",
                    "expenses": 5,
                    "comment": f"Заявка отменена как повторная (дубликат инцидента #{master_id}). Все работы ведутся в основной заявке. По вопросам звоните на 49-87.",
                    "is_redirect": False,
                    "rule_type": "duplicate_task",
                    "confidence": 0.99,
                    "decision_source": "db_dedup",
                }
            else:
                # 2. Быстрый прогон через модульный RuleEngine (Wi-Fi, Ремонт, Редирект, Принтер)
                decision = tr.auto_detect_template(
                    task=t,
                    diag=rule_diag,
                    kb_matches=None,
                    redirect_mode=redirect_only,
                )

                # 3. Если правило общее/стандартное и запрошен RAG — ищем семантическое решение в pgvector RAG
                if include_rag and decision.get("rule_type") in ("standard_in_work", None) and not decision.get("is_redirect"):
                    kb_matches = await tr.search_knowledge_base(
                        db=db,
                        query_text=query_text,
                        limit=2,
                        distance_threshold=0.70,
                        circuit=circuit_dec.circuit,
                        metadata=routing_metadata,
                    )
                    if kb_matches:
                        decision = tr.auto_detect_template(
                            task=t,
                            diag=rule_diag,
                            kb_matches=kb_matches,
                            redirect_mode=redirect_only,
                        )
                        decision["decision_source"] = "rag_consensus"
                    else:
                        decision["decision_source"] = "standard_fallback"
                else:
                    decision["decision_source"] = "rule_engine" if decision.get("rule_type") != "standard_in_work" else "standard_fallback"

            # Флаг готовности решения AI
            has_ai_solution = bool(
                decision and (
                    decision.get("rule_type") != "standard_in_work"
                    or len(kb_matches) > 0
                )
            )

            meta = t.get("_field_meta") or {}
            pc_name = meta.get("pc_name") or t.get("pc_name") or ""
            if not pc_name and t.get("Data"):
                from shared.normalizer import extract_pc_names_from_text
                pcs = extract_pc_names_from_text(t["Data"])
                if pcs:
                    pc_name = pcs[0]

            s_id = t.get("ServiceId")
            s_info = catalog_map.get(s_id, {})
            resolved_service_name = t.get("ServiceName") or s_info.get("name") or "Общие вопросы"
            root_service_id = s_info.get("root_id")
            root_service_name = s_info.get("root_name") or "Общие вопросы"

            # Расчет Confidence Score для предотвращения слепого одобрения (Rubber Stamping)
            confidence = calculate_confidence_score(
                kb_matches=kb_matches,
                telemetry=telemetry,
                rule_decision=decision,
            )
            if decision:
                decision["confidence"] = confidence

            result_items.append({
                "task": t,
                "task_id": t_id,
                "name": t.get("Name"),
                "created": t.get("Created"),
                "status_id": t.get("StatusId"),
                "status_name": t.get("StatusName"),
                "service_id": s_id,
                "service_name": resolved_service_name,
                "root_service_id": root_service_id,
                "root_service_name": root_service_name,
                "creator": t.get("Creator"),
                "creator_phone": meta.get("phone") or t.get("CreatorPhone") or "—",
                "executors": t.get("Executors") or t.get("Executor") or "",
                "executor_ids": t.get("ExecutorIds") or ([t["ExecutorId"]] if t.get("ExecutorId") else []),
                "pc_name": pc_name,
                "room": meta.get("room") or "",
                "has_attachments": t.get("_has_attachments", False),
                "attachments_count": len(t.get("_attachments_list", [])),
                "attachments": t.get("_attachments_list", []),
                "suggested_action": decision,
                "is_duplicate": is_dup,
                "duplicate_info": dup_info,
                "kb_matches": kb_matches,
                "telemetry": telemetry,
                "circuit": circuit_dec.circuit.value,
                "circuit_reason": circuit_dec.reason,
                "requires_sanitization": circuit_dec.requires_sanitization,
                "has_ai_solution": has_ai_solution,
                "confidence_score": confidence,
                "requires_human_review": bool(confidence < 0.80),
            })

        return {
            "total_open": len(active_tasks),
            "filter_id": filter_id,
            "page": page,
            "tasks": result_items,
            "duplicates": all_duplicates[:10],
            "root_services": [
                {"id": v["id"], "name": v["name"], "num": k}
                for k, v in sorted(ROOT_SERVICES.items())
            ],
            "services_catalog": list(catalog_map.values()),
            "is_truncated": len(tasks) >= fetch_limit,
        }

    @classmethod
    async def get_task_card_details(
        cls,
        service_auth_b64: str,
        db: AsyncSession,
        task_id: int,
        force: bool = False,
    ) -> dict[str, Any] | None:
        """
        Возвращает расширенную карточку задачи с нормализацией, историей переписки,
        RAG-совпадениями, телеметрией и AI-синтезом решения.
        """
        import app.routers.triage as tr

        task = await intraservice.get_single_task(service_auth_b64, task_id)
        if not task:
            return None

        # Обогащаем имя сервиса из каталога
        catalog_map = await cls.get_service_catalog_map(service_auth_b64)
        s_id = task.get("ServiceId")
        if s_id:
            s_info = catalog_map.get(s_id, {})
            if not task.get("ServiceName") and s_info.get("name"):
                task["ServiceName"] = s_info["name"]
            task["RootServiceId"] = s_info.get("root_id")
            task["RootServiceName"] = s_info.get("root_name")

        raw_history = await intraservice.get_task_lifetime(service_auth_b64, task_id) or []
        if isinstance(raw_history, dict):
            history = raw_history.get("TaskLifetimes") or []
        elif isinstance(raw_history, list):
            history = raw_history
        else:
            history = []

        telemetry = await tr.get_task_telemetry(task_id)
        if telemetry is None:
            telemetry = await tr.prefetch_task_telemetry(task)
        rule_diag = cls._telemetry_to_rule_diag(telemetry)

        t_name = task.get("Name") or ""
        t_desc = task.get("Description") or ""
        query_text = f"{t_name}. {t_desc}".strip()

        routing_metadata = RoutingMetadata(service_id=task.get("ServiceId"))
        circuit_dec = data_sanitizer.evaluate_circuit(
            prompt=query_text,
            metadata=routing_metadata,
        )

        # Проверяем редирект в другой отдел
        is_redirect = bool(tr.detect_service_redirect(task))
        kb_matches = []
        if not is_redirect:
            kb_matches = await tr.search_knowledge_base(
                db=db,
                query_text=query_text,
                limit=3,
                distance_threshold=0.70,
                circuit=circuit_dec.circuit,
                metadata=routing_metadata,
            )

        decision = tr.auto_detect_template(
            task=task,
            diag=rule_diag,
            kb_matches=kb_matches,
        )

        # Ключ зависит от фактического содержимого, а не только от количества
        # комментариев: редактирование описания/реплики не вернет устаревший ответ.
        ai_resolution = None
        redis = tr.get_redis_client()
        cache_payload = json.dumps(
            {
                "task": {
                    "name": t_name,
                    "description": t_desc,
                    "service_id": s_id,
                },
                "history": history,
                "decision": decision,
                "telemetry_collected_at": (telemetry or {}).get("collected_at"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        cache_digest = hashlib.sha256(cache_payload.encode("utf-8")).hexdigest()[:20]
        cache_key = f"ai:resolution:{task_id}:{cache_digest}"
        if force:
            try:
                keys = await redis.keys(f"ai:resolution:{task_id}:*")
                if keys:
                    await redis.delete(*keys)
            except Exception:
                pass
        else:
            try:
                cached_res = await redis.get(cache_key)
                if cached_res:
                    ai_resolution = cached_res
            except Exception:
                pass

        if ai_resolution is None and not is_redirect and decision.get("rule_type") != "duplicate_task":
            ai_resolution = await tr.synthesize_triage_resolution(
                task=task,
                kb_matches=kb_matches,
                telemetry=telemetry,
                circuit=circuit_dec.circuit,
                rule_decision=decision,
                comments_history=history,
            )
            if ai_resolution:
                try:
                    await redis.set(cache_key, ai_resolution, ex=3600)
                except Exception:
                    pass

        card_confidence = calculate_confidence_score(
            kb_matches=kb_matches,
            telemetry=telemetry,
            rule_decision=decision,
        )
        if decision:
            decision["confidence"] = card_confidence

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
            "has_ai_solution": bool(len(kb_matches) > 0 or (decision and decision.get("rule_type") != "standard_in_work")),
            "confidence_score": card_confidence,
            "requires_human_review": bool(card_confidence < 0.80),
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
        operator_user_id: int | None = None,
        verified_execution_job_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Применяет решение к заявке/группе заявок:
        1. Перевод в статус 27 (В работе) при необходимости.
        2. Перевод в целевой статус (29, 30, 35, 48) с комментарием.
        3. Списание трудозатрат от имени авторизованного оператора.
        4. Автообучение pgvector RAG при подтвержденном закрытии.
        """
        import app.routers.triage as tr

        op_user_id = operator_user_id or settings.PRIMARY_EXECUTOR_ID
        exec_ids = executor_ids or (str(op_user_id) if op_user_id else settings.DEFAULT_EXECUTOR_IDS)
        results = []

        for tid in task_ids:
            if dry_run:
                results.append({
                    "task_id": tid,
                    "status": "simulated",
                    "target_status_id": status_id,
                    "update_ok": True,
                    "expenses_ok": True,
                })
                continue

            # Инфраструктурные рекомендации нельзя превращать в статус 29
            # только по нажатию Apply. Сначала должен существовать успешный
            # результат Execution Worker именно для этой заявки и действия.
            task_snapshot = None
            if status_id == 29:
                try:
                    task_snapshot = await intraservice.get_single_task(
                        service_auth_b64, tid
                    )
                    # Проверяем исполняемые intent напрямую, независимо от
                    # приоритета ServiceRedirectRule в основном пайплайне.
                    execution_decision = (
                        CredentialsRule().evaluate(task_snapshot)
                        if task_snapshot
                        else None
                    )
                    rule_decision = (
                        execution_decision.to_dict() if execution_decision else {}
                    )
                    rule_type = rule_decision.get("rule_type")
                    expected_actions = TriageService._EXECUTION_RULE_ACTIONS.get(
                        rule_type
                    )
                    if expected_actions:
                        proof_ok, proof_error = await TriageService._validate_execution_proof(
                            verified_execution_job_id,
                            tid,
                            expected_actions,
                        )
                        if not proof_ok:
                            results.append({
                                "task_id": tid,
                                "status": "failed",
                                "update_ok": False,
                                "expenses_ok": False,
                                "error": proof_error,
                            })
                            continue
                except Exception as exc:
                    logger.exception(
                        "Ошибка проверки условий финализации заявки #%d: %s", tid, exc
                    )
                    results.append({
                        "task_id": tid,
                        "status": "failed",
                        "update_ok": False,
                        "expenses_ok": False,
                        "error": "Не удалось безопасно проверить условия финализации заявки.",
                    })
                    continue

            # 1. При необходимости берем в работу (27)
            if status_id != 27:
                in_work_ok = await intraservice.update_task_full(
                    auth_b64=service_auth_b64,
                    task_id=tid,
                    status_id=27,
                    executor_ids=exec_ids,
                )
                if not in_work_ok:
                    results.append({
                        "task_id": tid,
                        "status": "failed",
                        "update_ok": False,
                        "expenses_ok": False,
                        "error": "Не удалось перевести заявку в обязательный промежуточный статус «В работе».",
                    })
                    continue

            # 2. Обновление в целевой статус
            upd_ok = await intraservice.update_task_full(
                auth_b64=service_auth_b64,
                task_id=tid,
                status_id=status_id,
                comment=comment if comment else None,
                executor_ids=exec_ids,
            )

            # 3. Списание трудозатрат от имени авторизованного оператора
            exp_ok = True
            if expenses and expenses > 0:
                exp_ok = await intraservice.add_task_expenses(
                    auth_b64=service_auth_b64,
                    task_id=tid,
                    minutes=expenses,
                    user_id=op_user_id,
                )

            # 4. Авто-индексация RAG (строго только при успешном обновлении тикета в IntraService)
            clean_comment = comment.strip() if comment else ""
            should_index = False
            if upd_ok:
                if status_id == 29 and clean_comment:
                    should_index = True
                elif status_id == 30 and len(clean_comment) >= 35:
                    if not clean_comment.startswith("Заявка переведена в статус Отменена"):
                        should_index = True

            if should_index:
                try:
                    task_data = task_snapshot or await intraservice.get_single_task(service_auth_b64, tid)
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

            # 5. Сохранение записи аудита в TriageAuditLog (Feedback Loop)
            if upd_ok and db:
                try:
                    from app.database.db import TriageAuditLog
                    audit_entry = TriageAuditLog(
                        task_id=tid,
                        generated_comment=None,
                        final_comment=clean_comment or f"Статус {status_id}",
                        confidence_score=1.0,
                        diff_ratio=0.0,
                        operator_id=str(op_user_id) if op_user_id else None,
                        status_id=status_id,
                    )
                    add_result = db.add(audit_entry)
                    # AsyncSession.add синхронный; awaitable встречается только
                    # у тестовых/адаптерных сессий и поддерживается без warning.
                    if inspect.isawaitable(add_result):
                        await add_result
                    await db.commit()
                except Exception as e:
                    logger.debug("Ошибка сохранения TriageAuditLog для заявки #%d: %s", tid, e)

            results.append({
                "task_id": tid,
                "status": "success" if (upd_ok and exp_ok) else ("failed" if not upd_ok else "partial_failure"),
                "update_ok": upd_ok,
                "expenses_ok": exp_ok,
                "error": None if upd_ok else "Ошибка обновления статуса/комментария заявки в IntraService (проверьте доступные переходы статусов и права роли).",
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
