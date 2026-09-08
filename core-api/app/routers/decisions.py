from datetime import datetime, timezone
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.domain import FactObservation, FactSource, FactState
from shared.normalizer import is_valid_pc_name, is_valid_printer_name, normalize_pc_name
from app.database.db import DecisionFeedback, DecisionRecord, DecisionStep, TicketRun, get_db
from app.routers.deps import get_service_auth_b64, principal_subject, require_permission, verify_trusted_origin
from app.services.decision_journal import DecisionJournalService, serialize_decision
from app.services.facts.store import TicketFactStore
from app.services.scenario_decision import ScenarioDecisionService
from app.services.ticket_runs import TicketRunMode, TicketRunService, TicketRunState
from app.routers.ticket_runs import serialize_run


router = APIRouter(prefix="/api/v2", tags=["Decision journal"])


class FeedbackRequest(BaseModel):
    verdict: Literal["accepted", "modified", "rejected", "correct", "partial", "incorrect", "insufficient_data"]
    reason_code: str | None = Field(None, max_length=64)
    comment: str | None = Field(None, max_length=2_000)
    final_action: dict[str, Any] = Field(default_factory=dict)


class FactOverrideRequest(BaseModel):
    expected_decision_version: int | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    current_draft_text: str | None = None


@router.get("/tasks/{task_id}/decisions")
async def list_task_decisions(
    task_id: int,
    limit: int = Query(20, ge=1, le=100),
    before_version: int | None = Query(None, ge=1),
    _triage=Depends(require_permission("triage:read")),
    _audit=Depends(require_permission("audit:read")),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(DecisionRecord).where(DecisionRecord.task_id == task_id)
    if before_version is not None:
        stmt = stmt.where(DecisionRecord.version < before_version)
    records = list(
        (await db.scalars(stmt.order_by(desc(DecisionRecord.version)).limit(limit))).all()
    )
    return {
        "items": [serialize_decision(item) for item in records],
        "next_before_version": records[-1].version if len(records) == limit else None,
    }


@router.get("/decisions/{decision_id}")
async def get_decision(
    decision_id: uuid.UUID,
    _triage=Depends(require_permission("triage:read")),
    _audit=Depends(require_permission("audit:read")),
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(DecisionRecord, decision_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "decision_not_found")
    steps = list(
        (
            await db.scalars(
                select(DecisionStep)
                .where(DecisionStep.decision_id == decision_id)
                .order_by(DecisionStep.sequence)
            )
        ).all()
    )
    feedback = list(
        (
            await db.scalars(
                select(DecisionFeedback)
                .where(DecisionFeedback.decision_id == decision_id)
                .order_by(DecisionFeedback.created_at)
            )
        ).all()
    )
    result = serialize_decision(record, steps=steps)
    result["feedback"] = [
        {
            "id": str(item.id),
            "verdict": item.verdict,
            "reason_code": item.reason_code,
            "comment": item.comment,
            "final_action": item.final_action_json,
            "actor": item.actor,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in feedback
    ]
    return result


@router.post("/decisions/{decision_id}/feedback", status_code=status.HTTP_201_CREATED)
async def create_feedback(
    decision_id: uuid.UUID,
    payload: FeedbackRequest,
    actor: str = Depends(principal_subject),
    _permission=Depends(require_permission("triage:mutate")),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    item = await DecisionJournalService(db).add_feedback(
        decision_id=decision_id,
        verdict=payload.verdict,
        reason_code=payload.reason_code,
        comment=payload.comment,
        final_action=payload.final_action,
        actor=actor,
    )
    return {
        "id": str(item.id),
        "decision_id": str(item.decision_id),
        "verdict": item.verdict,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


@router.post("/tasks/{task_id}/override-facts", status_code=status.HTTP_200_OK)
async def override_task_facts(
    task_id: int,
    payload: FactOverrideRequest,
    actor: str = Depends(principal_subject),
    service_auth_b64: str = Depends(get_service_auth_b64),
    _permission=Depends(require_permission("triage:mutate")),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    """Корректировка и переопределение фактов сценария оператором (Fact-Override)."""
    from app.services.triage_service import TriageService

    card = await TriageService.get_task_card_details(
        service_auth_b64=service_auth_b64,
        db=db,
        task_id=task_id,
    )
    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Заявка #{task_id} не найдена в IntraService.",
        )

    # Валидация оптимистичной блокировки версии решения
    if payload.expected_decision_version is not None:
        latest_record = await db.scalar(
            select(DecisionRecord)
            .where(DecisionRecord.task_id == task_id)
            .order_by(desc(DecisionRecord.version))
        )
        if latest_record is not None and latest_record.version > payload.expected_decision_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Версия решения изменилась (ожидалась {payload.expected_decision_version}, в базе {latest_record.version}). Обновите карточку.",
            )

    # Валидация и нормализация переданных фактов
    observations: list[FactObservation] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for raw_key, raw_val in payload.facts.items():
        key = raw_key.strip()
        if raw_val is None or str(raw_val).strip() == "":
            observations.append(
                FactObservation(
                    key=key,
                    value=None,
                    state=FactState.MISSING,
                    source=FactSource.OPERATOR,
                    source_ref=f"operator:{actor}",
                    observed_at=now_iso,
                )
            )
            continue

        val_str = str(raw_val).strip()
        if key == "pc_name":
            norm_name = normalize_pc_name(val_str)
            if not is_valid_pc_name(norm_name):
                raise HTTPException(
                    status_code=422,
                    detail=f"Недопустимое сетевое имя компьютера: '{val_str}'",
                )
            val_str = norm_name
        elif key in {"printer_address", "printer_ip"}:
            if not is_valid_printer_name(val_str) and not any(c.isalnum() for c in val_str):
                raise HTTPException(
                    status_code=422,
                    detail=f"Недопустимый сетевой адрес или модель принтера: '{val_str}'",
                )

        observations.append(
            FactObservation(
                key=key,
                value=val_str,
                state=FactState.VALID,
                source=FactSource.OPERATOR,
                source_ref=f"operator:{actor}",
                evidence_span=f"Ручной ввод оператора: {val_str}",
                observed_at=now_iso,
            )
        )

    # Получаем или регистрируем TicketRun
    run_service = TicketRunService(db)
    run = await run_service.get_latest_for_task(task_id)
    if run is None:
        run = TicketRun(
            task_id=task_id,
            mode=TicketRunMode.MANUAL.value,
            state=TicketRunState.RUNNING.value,
            trigger_kind="operator_override",
            trigger_key=f"task:{task_id}:override:{uuid.uuid4().hex[:8]}",
            trigger_snapshot_json={"source": "operator_override", "actor": actor},
            current_step="operator_override",
            scenario_key=card.get("suggested_action", {}).get("scenario_key") or "clarification",
            scenario_version=1,
            fact_revision=0,
            decision_version=0,
            created_by=actor,
            updated_by=actor,
        )
        db.add(run)
        await db.flush()
        await run_service._append_run_event(
            run,
            event_type="run_created",
            actor=actor,
            details={"mode": run.mode, "source": "operator_override"},
        )

    fact_store = TicketFactStore(db)
    await fact_store.append(run.id, observations)
    all_observations = await fact_store.load(run.id)

    # Пересчитываем решение через ScenarioDecisionService
    next_decision_version = max(run.decision_version or 0, payload.expected_decision_version or 0) + 1
    envelope = await ScenarioDecisionService(db).analyze(
        task=card.get("task") or {},
        comments=card.get("history") or [],
        diagnostics=card.get("telemetry"),
        kb_matches=card.get("kb_matches"),
        legacy_decision=card.get("suggested_action"),
        generated_response=payload.current_draft_text or card.get("ai_suggested_resolution"),
        fact_revision=run.fact_revision + 1,
        decision_version=next_decision_version,
        observations=all_observations,
    )

    run.fact_revision += 1
    run.decision_version = envelope.decision_version
    await run_service._append_run_event(
        run,
        event_type="facts_overridden",
        actor=actor,
        details={
            "overridden_facts": payload.facts,
            "decision_version": envelope.decision_version,
            "outcome_kind": envelope.outcome.kind,
        },
    )
    await db.commit()
    await db.refresh(run)

    return {
        "success": True,
        "task_id": task_id,
        "decision_envelope": envelope.model_dump(mode="json"),
        "run": serialize_run(run),
        "facts_summary": envelope.facts_summary,
        "outcome": envelope.outcome.model_dump(mode="json"),
        "confidence": envelope.confidence,
    }
