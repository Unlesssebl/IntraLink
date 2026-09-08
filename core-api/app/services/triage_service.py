"""
Доменный сервис триажа, оркестрации пачек заявок, рекомендаций и применения решений.
Инкапсулирует бизнес-логику для предотвращения разрастания роутеров (SRP).
"""

import asyncio
import hashlib
import json
import logging
import time
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from app.config import settings
from app.services import intraservice
from app.services.ai import RoutingMetadata, data_sanitizer
from app.services.ai_synthesis import calculate_confidence_score
from app.services.deduplication import DuplicateDetector
from app.services.fact_extractor import enrich_task_with_extracted_facts
from app.services.typed_decision_adapter import materialize_typed_decision
from app.services.decision_envelope import envelope_to_legacy
from app.services.scenario_decision import ScenarioDecisionService
from app.services.rules.credentials import CredentialsRule
from app.services.rules.catalog import (
    ROOT_SERVICES,
    get_root_number_for_service_id,
)
from app.services.template_engine import (
    auto_detect_template,
    detect_service_redirect,
)
from app.services.rag import search_knowledge_base
from app.services.triage_session import TriageSessionManager

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
        db: AsyncSession | None = None,
    ) -> tuple[bool, str | None]:
        if not job_id:
            return (
                False,
                "Для статуса «Выполнена» требуется подтверждение успешной команды Execution Worker.",
            )
        try:
            from app.database.db import CommandRecord

            command = (
                await db.get(CommandRecord, uuid.UUID(str(job_id)))
                if db is not None
                else None
            )
        except (TypeError, ValueError):
            command = None
        if command is not None:
            if command.status != "succeeded":
                return False, f"Команда '{job_id}' не завершена успешно."
            if command.task_id != task_id:
                return False, f"Команда '{job_id}' относится к другой заявке."
            if command.action not in expected_actions:
                return False, f"Команда '{job_id}' не подтверждает требуемое действие."
            return True, None
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
    async def get_service_catalog_map(
        cls, service_auth_b64: str
    ) -> dict[int, dict[str, Any]]:
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
                        root_num = get_root_number_for_service_id(
                            parent_id
                        ) or root_id_to_num.get(parent_id)

                    root_info = ROOT_SERVICES.get(root_num) if root_num else None
                    if not root_info and parent_id and parent_id in root_id_to_num:
                        root_num = root_id_to_num[parent_id]
                        root_info = ROOT_SERVICES.get(root_num)

                    cat_map[sid] = {
                        "id": sid,
                        "name": sname,
                        "parent_id": parent_id,
                        "root_num": root_num,
                        "root_id": root_info.get("id")
                        if root_info
                        else (parent_id or sid),
                        "root_name": root_info.get("name")
                        if root_info
                        else (sname if not parent_id else "Общие вопросы"),
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
        compute_recommendations: bool = False,
    ) -> dict[str, Any]:
        """
        Возвращает подготовленную пачку заявок с авто-подбором шаблонов Rule Engine,
        детекцией дубликатов, семантическим RAG контекстом и телеметрией 0ms.
        """

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
            else await TriageSessionManager.get_skipped_task_ids(operator_id)
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
                s_name = (
                    t.get("ServiceName") or catalog_map.get(s_id, {}).get("name") or ""
                ).lower()
                if root_num and root_num == p_clean:
                    filtered.append(t)
                elif p_clean.lower() in s_name:
                    filtered.append(t)
            active_tasks = filtered

        # Фильтрация только редиректов
        if redirect_only:
            active_tasks = [t for t in active_tasks if detect_service_redirect(t)]

        # Пагинация
        start_idx = (page - 1) * limit
        page_tasks = active_tasks[start_idx : start_idx + limit]

        result_items = []
        for t in page_tasks:
            t = await enrich_task_with_extracted_facts(t)
            t_id = t.get("Id")
            t_name = t.get("Name") or ""
            t_desc = t.get("Description") or ""
            query_text = f"{t_name}. {t_desc}".strip()

            routing_metadata = RoutingMetadata(service_id=t.get("ServiceId"))
            circuit_dec = data_sanitizer.evaluate_circuit(
                prompt=query_text,
                metadata=routing_metadata,
            )

            telemetry = None
            rule_diag: dict[str, Any] = {}
            if compute_recommendations:
                # Вычисление рекомендаций разрешено только из явного mutating
                # endpoint. Обычная загрузка очереди остаётся read-only.
                import app.routers.triage as tr

                telemetry = await tr.get_task_telemetry(t_id)
                if telemetry is None:
                    asyncio.create_task(tr.prefetch_task_telemetry(t))
                rule_diag = cls._telemetry_to_rule_diag(telemetry)

            is_dup = t_id in dup_map
            dup_info = dup_map.get(t_id)

            kb_matches = []
            decision = None

            envelope = None
            # Compatibility mode remains available to non-HTTP callers while
            # they migrate to explicit analyse endpoints.
            if compute_recommendations and is_dup:
                master_id = (dup_info or {}).get("master_task_id", "")
                decision = {
                    "template_key": "duplicate_task",
                    "name": f"Отмена дубликата (привязка к #{master_id})"
                    if master_id
                    else "Отмена дубликата",
                    "status_id": 30,
                    "status_name": "Отменена",
                    "expenses": 5,
                    "comment": f"Заявка отменена как повторная (дубликат инцидента #{master_id}). Все работы ведутся в основной заявке. По вопросам звоните на 49-87.",
                    "is_redirect": False,
                    "rule_type": "duplicate_task",
                    "confidence": 0.99,
                    "decision_source": "db_dedup",
                }
            elif compute_recommendations:
                # 2. Единая доменная точка принятия решений: ScenarioDecisionService (SSOT)
                is_redirect_hint = bool(detect_service_redirect(t))
                if include_rag and not is_redirect_hint and not redirect_only:
                    kb_matches = await search_knowledge_base(
                        db=db,
                        query_text=query_text,
                        limit=2,
                        distance_threshold=0.70,
                        circuit=circuit_dec.circuit,
                        metadata=routing_metadata,
                        service_id=t.get("ServiceId"),
                    )

                try:
                    envelope = await ScenarioDecisionService(db).analyze(
                        task=t,
                        diagnostics=rule_diag,
                        kb_matches=kb_matches,
                    )
                    decision = envelope_to_legacy(envelope)
                    decision["_decision_envelope"] = envelope.model_dump(mode="json")
                    if envelope.scenario_key == "rag_consultation" and kb_matches:
                        decision["decision_source"] = "rag_consensus"
                    elif envelope.scenario_key in ("consultation", "standard_in_work"):
                        decision["decision_source"] = "standard_fallback"
                    else:
                        decision["decision_source"] = "scenario_engine"
                except Exception as exc:
                    logger.warning(
                        "ScenarioDecisionService failed for task %s, fallback to rule engine: %s",
                        t_id,
                        exc,
                    )
                    decision = auto_detect_template(
                        task=t,
                        diag=rule_diag,
                        kb_matches=kb_matches,
                        redirect_mode=redirect_only,
                    )
                    decision = await materialize_typed_decision(db, decision)
                    decision["decision_source"] = "rule_engine"

            sources = {
                "rule": bool(
                    decision
                    and decision.get("rule_type")
                    not in (
                        "rule.standard_in_work",
                        "standard_in_work",
                        "scenario.consultation",
                    )
                ),
                "rag": bool(kb_matches),
                "ai": False,
            }

            meta = t.get("_field_meta") or {}
            pc_name = meta.get("pc_name") or t.get("pc_name") or ""
            if not pc_name and t.get("Data"):
                from shared.normalizer import extract_pc_names_from_text

                pcs = extract_pc_names_from_text(t["Data"])
                if pcs:
                    pc_name = pcs[0]

            s_id = t.get("ServiceId")
            s_info = catalog_map.get(s_id, {})
            resolved_service_name = (
                t.get("ServiceName") or s_info.get("name") or "Общие вопросы"
            )
            root_service_id = s_info.get("root_id")
            root_service_name = s_info.get("root_name") or "Общие вопросы"

            # Расчет Confidence Score для предотвращения слепого одобрения (Rubber Stamping)
            confidence = (
                envelope.confidence
                if envelope is not None
                else calculate_confidence_score(
                    kb_matches=kb_matches,
                    telemetry=telemetry,
                    rule_decision=decision,
                )
                if compute_recommendations
                else 0.0
            )
            if decision:
                decision["confidence"] = confidence

            result_items.append(
                {
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
                    "executor_ids": t.get("ExecutorIds")
                    or ([t["ExecutorId"]] if t.get("ExecutorId") else []),
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
                    "sources": sources,
                    "readiness": {
                        "ready": bool(decision),
                        "blocked_reasons": [],
                    },
                    "confidence_score": confidence,
                    "requires_human_review": bool(confidence < 0.80),
                }
            )

        return {
            "total_open": len(active_tasks),
            "scope_task_ids": [
                int(task["Id"]) for task in active_tasks if task.get("Id")
            ],
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
    async def get_task_card_snapshot(
        cls,
        service_auth_b64: str,
        task_id: int,
    ) -> dict[str, Any] | None:
        """Read current IntraService data without Rule Engine, RAG or AI."""
        task = await intraservice.get_single_task(service_auth_b64, task_id)
        if not task:
            return None

        catalog_map = await cls.get_service_catalog_map(service_auth_b64)
        service_id = task.get("ServiceId")
        if service_id:
            service_info = catalog_map.get(service_id, {})
            if not task.get("ServiceName") and service_info.get("name"):
                task["ServiceName"] = service_info["name"]
            task["RootServiceId"] = service_info.get("root_id")
            task["RootServiceName"] = service_info.get("root_name")

        task = await enrich_task_with_extracted_facts(task)
        raw_history = (
            await intraservice.get_task_lifetime(service_auth_b64, task_id) or []
        )
        if isinstance(raw_history, dict):
            history = raw_history.get("TaskLifetimes") or []
        elif isinstance(raw_history, list):
            history = raw_history
        else:
            history = []
        return {"task": task, "history": history}

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

        task = await enrich_task_with_extracted_facts(task)

        raw_history = (
            await intraservice.get_task_lifetime(service_auth_b64, task_id) or []
        )
        if isinstance(raw_history, dict):
            history = raw_history.get("TaskLifetimes") or []
        elif isinstance(raw_history, list):
            history = raw_history
        else:
            history = []

        import app.routers.triage as tr

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
        is_redirect = bool(detect_service_redirect(task))
        kb_matches = []
        if not is_redirect:
            kb_matches = await search_knowledge_base(
                db=db,
                query_text=query_text,
                limit=3,
                distance_threshold=0.70,
                circuit=circuit_dec.circuit,
                metadata=routing_metadata,
                service_id=task.get("ServiceId"),
            )

        decision = auto_detect_template(
            task=task,
            diag=rule_diag,
            kb_matches=kb_matches,
            comments_history=history,
        )
        decision = await materialize_typed_decision(db, decision)

        # Ключ зависит от фактического содержимого, а не только от количества
        # комментариев: редактирование описания/реплики не вернет устаревший ответ.
        ai_resolution = None
        ai_metadata: dict[str, Any] = {}
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
                "telemetry_status": (telemetry or {}).get("status")
                or (telemetry or {}).get("is_online"),
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

        if (
            ai_resolution is None
            and not is_redirect
            and decision.get("rule_type") != "duplicate_task"
        ):
            ai_resolution, ai_metadata = await tr.synthesize_triage_resolution(
                task=task,
                kb_matches=kb_matches,
                telemetry=telemetry,
                circuit=circuit_dec.circuit,
                rule_decision=decision,
                comments_history=history,
                return_metadata=True,
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

        decision_envelope = None
        try:
            envelope = await ScenarioDecisionService(db).analyze(
                task=task,
                comments=history,
                diagnostics=rule_diag,
                kb_matches=kb_matches,
                generated_response=ai_resolution,
                decision_version=int((decision or {}).get("version") or 1),
            )
            compiled_legacy = envelope_to_legacy(envelope)
            decision = {**(decision or {}), **compiled_legacy}
            ai_resolution = envelope.response_draft
            decision_envelope = envelope.model_dump(mode="json")
            decision["_decision_envelope"] = decision_envelope
            card_confidence = envelope.confidence
        except Exception:
            # Compatibility window is fail-safe: a new envelope failure must not
            # remove the already computed legacy operator recommendation.
            logger.exception("Scenario decision envelope failed for task %s", task_id)

        printer_address = ""
        for field in task.get("CustomFields", []) or []:
            field_id = field.get("CustomFieldId") or field.get("FieldId")
            if field_id == settings.PRINTER_IP_CUSTOM_FIELD_ID:
                printer_address = str(field.get("Value") or "").strip()
                if printer_address:
                    break
        if not printer_address:
            from app.services.lifecycle.intent_analyzer import IntentAnalyzer

            extracted = IntentAnalyzer.analyze_fast_regex(f"{t_name} {t_desc}")
            if extracted and extracted.extracted_ip:
                printer_address = extracted.extracted_ip

        blocked_reasons: list[str] = []
        if decision_envelope:
            envelope_outcome = decision_envelope.get("outcome") or {}
            if envelope_outcome.get("kind") == "clarification":
                blocked_reasons.extend(
                    f"missing_fact:{field}"
                    for field in envelope_outcome.get("missing_fields", [])
                )
                blocked_reasons.extend(
                    f"invalid_fact:{field}"
                    for field in envelope_outcome.get("invalid_fields", [])
                )
            elif envelope_outcome.get("kind") in {"manual_review", "no_match"}:
                blocked_reasons.append(
                    envelope_outcome.get("reason") or "manual_review"
                )
            if (decision_envelope.get("policy") or {}).get("resolution_error"):
                blocked_reasons.append("resolution_policy_unavailable")

        return {
            "task": task,
            "history": history,
            "kb_matches": kb_matches,
            "telemetry": telemetry,
            "suggested_action": decision,
            "ai_suggested_resolution": ai_resolution,
            "decision_envelope": decision_envelope,
            "ai_metadata": ai_metadata,
            "circuit": circuit_dec.circuit.value,
            "circuit_reason": circuit_dec.reason,
            "requires_sanitization": circuit_dec.requires_sanitization,
            "sources": {
                "rule": bool(
                    decision and decision.get("rule_type") != "standard_in_work"
                ),
                "rag": bool(kb_matches),
                "ai": bool(ai_metadata.get("ai_used")),
            },
            "readiness": {
                "ready": bool(decision) and not blocked_reasons,
                "blocked_reasons": blocked_reasons,
            },
            "confidence_score": card_confidence,
            "requires_human_review": bool(card_confidence < 0.80 or blocked_reasons),
            "printer_address": printer_address,
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
        is_private: bool = False,
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
        exec_ids = executor_ids or (
            str(op_user_id) if op_user_id else settings.DEFAULT_EXECUTOR_IDS
        )
        results = []

        for tid in task_ids:
            if dry_run:
                results.append(
                    {
                        "task_id": tid,
                        "status": "simulated",
                        "target_status_id": status_id,
                        "update_ok": True,
                        "expenses_ok": True,
                    }
                )
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
                        (
                            proof_ok,
                            proof_error,
                        ) = await TriageService._validate_execution_proof(
                            verified_execution_job_id,
                            tid,
                            expected_actions,
                            db=db,
                        )
                        if not proof_ok:
                            results.append(
                                {
                                    "task_id": tid,
                                    "status": "failed",
                                    "update_ok": False,
                                    "expenses_ok": False,
                                    "error": proof_error,
                                }
                            )
                            continue
                except Exception as exc:
                    logger.exception(
                        "Ошибка проверки условий финализации заявки #%d: %s", tid, exc
                    )
                    results.append(
                        {
                            "task_id": tid,
                            "status": "failed",
                            "update_ok": False,
                            "expenses_ok": False,
                            "error": "Не удалось безопасно проверить условия финализации заявки.",
                        }
                    )
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
                    results.append(
                        {
                            "task_id": tid,
                            "status": "failed",
                            "update_ok": False,
                            "expenses_ok": False,
                            "error": "Не удалось перевести заявку в обязательный промежуточный статус «В работе».",
                        }
                    )
                    continue

            # 2. Обновление в целевой статус
            upd_ok = await intraservice.update_task_full(
                auth_b64=service_auth_b64,
                task_id=tid,
                status_id=status_id,
                comment=comment if comment else None,
                executor_ids=exec_ids,
                is_private=is_private,
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
                    if not clean_comment.startswith(
                        "Заявка переведена в статус Отменена"
                    ):
                        should_index = True

            if should_index:
                try:
                    task_data = task_snapshot or await intraservice.get_single_task(
                        service_auth_b64, tid
                    )
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

            results.append(
                {
                    "task_id": tid,
                    "status": "success"
                    if (upd_ok and exp_ok)
                    else ("failed" if not upd_ok else "partial_failure"),
                    "update_ok": upd_ok,
                    "expenses_ok": exp_ok,
                    "error": None
                    if upd_ok
                    else "Ошибка обновления статуса/комментария заявки в IntraService (проверьте доступные переходы статусов и права роли).",
                }
            )

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
