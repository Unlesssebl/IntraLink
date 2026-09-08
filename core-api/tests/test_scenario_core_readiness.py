"""
Regression and readiness tests for the Scenario Core Readiness Plan.
Verifies:
1. Atomic scenario transition (isolated savepoint, rollback safety).
2. Deferred events prefixing, absorption, and target drift pausing.
3. Operator fact history preservation and monotonic timestamps.
4. Unified manual & autopilot execution continuation.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.database.db import (
    ActionPolicyRecord,
    AsyncSessionLocal,
    AutopilotScenario,
    CommandEvent,
    CommandOutbox,
    CommandRecord,
    TicketFactObservation,
    TicketRun,
    TicketRunEvent,
)
from app.services.actions.registry import PolicyMode
from app.services.command_service import CommandService
from app.services.decision_journal import DecisionJournalService
from app.services.facts.merger import merge_observations
from app.services.facts.registry import FactRegistry, FactSpec, get_fact_registry
from app.services.facts.store import TicketFactStore
from app.services.scenario_orchestrator import TicketRunOrchestrator
from app.services.ticket_runs import TicketRunMode, TicketRunService, TicketRunState
from shared.domain import (
    DecisionEnvelope,
    FactObservation,
    FactSource,
    FactState,
    ResolutionProposed,
)


@pytest.fixture
def custom_fact_registry() -> FactRegistry:
    reg = FactRegistry()
    reg.register(FactSpec(key="device.pc_name"))
    reg.register(FactSpec(key="device.printer_ip"))
    return reg


@pytest.mark.asyncio
async def test_atomic_scenario_advance_rolls_back_command_on_event_failure():
    """Проверяет, что при сбое фиксации шага advance созданная команда откатывается целиком."""
    async with AsyncSessionLocal() as db:
        run = await TicketRunService(db).start_manual(
            task_id=98701,
            status_id=31,
            allowed_status_ids={31},
            actor="operator:test",
            trigger_key="manual:98701:atomic",
        )
        task = {
            "Id": 98701,
            "Name": "Настройка сетевого принтера",
            "Description": "Установить принтер на NTEMW0101, IP 10.244.15.10",
            "StatusId": 31,
        }

        orchestrator = TicketRunOrchestrator(db)
        original_append = orchestrator.runs._append_run_event

        async def broken_append(run_obj, *args, **kwargs):
            if kwargs.get("event_type") == "scenario_advanced":
                raise RuntimeError("database_event_write_failure")
            return await original_append(run_obj, *args, **kwargs)

        from shared.domain import ActionProposed, DecisionEnvelope
        fake_envelope = DecisionEnvelope(
            decision_version=1,
            scenario_key="test_scenario",
            scenario_version=1,
            facts_revision=1,
            facts_summary={},
            candidates=[],
            outcome=ActionProposed(
                rule_key="test",
                rule_version="1",
                outcome_key="printer_proposed",
                action="install_printer",
                parameters={"pc_name": "NTEMW0101", "printer_name": "HP", "printer_ip": "10.244.15.10"},
                risk_level=1,
                evidence=[],
            ),
            policy={},
            response_draft="",
            evidence_refs=[],
            confidence=1.0,
            requires_approval=False,
            status="proposed",
        )

        run_id = run.id
        with (
            patch.object(orchestrator.decisions, "analyze", new=AsyncMock(return_value=fake_envelope)),
            patch.object(orchestrator.runs, "_append_run_event", side_effect=broken_append),
        ):
            with pytest.raises(RuntimeError, match="database_event_write_failure"):
                await orchestrator.advance(
                    run_id=run_id,
                    task=task,
                    comments=[],
                    actor="test_actor",
                )

        await db.rollback()

        async with AsyncSessionLocal() as check_db:
            commands = (
                await check_db.scalars(
                    select(CommandRecord).where(CommandRecord.ticket_run_id == run_id)
                )
            ).all()
            assert len(commands) == 0


@pytest.mark.asyncio
async def test_deferred_event_has_prefix_and_does_not_break_subsequent_dedup():
    """Проверяет, что отложенные события имеют префикс deferred: и корректно поглощаются."""
    async with AsyncSessionLocal() as db:
        run = await TicketRunService(db).start_manual(
            task_id=98702,
            status_id=31,
            allowed_status_ids={31},
            actor="operator:test",
            trigger_key="manual:98702:deferred",
        )
        task = {
            "Id": 98702,
            "Name": "Установка принтера",
            "Description": "Принтер на NTEMW0102 IP 10.244.15.11",
            "StatusId": 31,
        }

        # Создаем активную команду для запуска
        service = CommandService(db)
        decision = await DecisionJournalService(db).record_operational(
            task_id=98702,
            ticket_run_id=run.id,
            action="install_printer",
            target={"task_id": 98702},
            parameters={"pc_name": "NTEMW0102", "printer_ip": "10.244.15.11", "printer_name": "HP"},
            actor="operator:test",
        )
        cmd, _ = await service.create(
            action="install_printer",
            target={"task_id": 98702},
            parameters={"pc_name": "NTEMW0102", "printer_ip": "10.244.15.11", "printer_name": "HP"},
            idempotency_key="cmd-defer-98702",
            initiator="operator:test",
            source="web",
            priority=5,
            ticket_run_id=run.id,
            decision_id=decision.id,
            decision_version=decision.version,
        )

        orchestrator = TicketRunOrchestrator(db)
        # При активной команде вызов advance должен создать ticket_event_deferred с префиксом deferred:
        res = await orchestrator.advance(
            run_id=run.id,
            task=task,
            comments=[{"Editor": "User", "Comments": "Жду установку"}],
            event_type="comment_added",
            actor="test_poller",
        )
        assert res.command is not None

        # Проверяем запись в БД
        deferred_event = await db.scalar(
            select(TicketRunEvent).where(
                TicketRunEvent.ticket_run_id == run.id,
                TicketRunEvent.event_type == "ticket_event_deferred",
            )
        )
        assert deferred_event is not None
        assert deferred_event.event_key.startswith("deferred:")
        assert deferred_event.details_json.get("source_event_key") is not None

        # Завершаем команду
        cmd.status = "succeeded"
        cmd.completed_at = dt.datetime.now(dt.timezone.utc)
        await db.commit()

        # Проверяем, что _get_pending_deferred_events видит это событие
        pending = await orchestrator._get_pending_deferred_events(run.id)
        assert len(pending) == 1

        # Следующий advance сверяет команду и поглощает отложенные события
        res2 = await orchestrator.advance(
            run_id=run.id,
            task=task,
            comments=[{"Editor": "User", "Comments": "Жду установку"}],
            event_type="comment_added",
            actor="test_poller",
        )
        # Проверяем, что отложенные события теперь поглощены
        pending_after = await orchestrator._get_pending_deferred_events(run.id)
        assert len(pending_after) == 0


@pytest.mark.asyncio
async def test_target_drift_during_execution_pauses_run_instead_of_finalizing():
    """Проверяет, что при смене целевого ПК во время выполнения команды запуск ставится на паузу."""
    async with AsyncSessionLocal() as db:
        run = await TicketRunService(db).start_manual(
            task_id=98703,
            status_id=31,
            allowed_status_ids={31},
            actor="operator:test",
            trigger_key="manual:98703:drift",
        )
        # Исходная заявка на PC-OLD
        task_old = {
            "Id": 98703,
            "Name": "Установка принтера",
            "Description": "Установить принтер на NTEMW0100 IP 10.244.15.20",
            "StatusId": 31,
        }

        service = CommandService(db)
        decision = await DecisionJournalService(db).record_operational(
            task_id=98703,
            ticket_run_id=run.id,
            action="install_printer",
            target={"task_id": 98703, "pc_name": "NTEMW0100"},
            parameters={"pc_name": "NTEMW0100", "printer_ip": "10.244.15.20", "printer_name": "Kyocera"},
            actor="operator:test",
        )
        cmd, _ = await service.create(
            action="install_printer",
            target={"task_id": 98703, "pc_name": "NTEMW0100"},
            parameters={"pc_name": "NTEMW0100", "printer_ip": "10.244.15.20", "printer_name": "Kyocera"},
            idempotency_key="cmd-drift-98703",
            initiator="operator:test",
            source="web",
            priority=5,
            ticket_run_id=run.id,
            decision_id=decision.id,
            decision_version=decision.version,
        )

        orchestrator = TicketRunOrchestrator(db)

        # Во время выполнения команды заявитель написал: "Ой, я пересел за NTEMW0999!"
        task_new = {
            "Id": 98703,
            "Name": "Установка принтера",
            "Description": "Установить принтер на NTEMW0999 IP 10.244.15.20",
            "StatusId": 31,
        }
        await orchestrator.advance(
            run_id=run.id,
            task=task_new,
            comments=[{"Editor": "Заявитель", "Comments": "Мой новый ПК NTEMW0999"}],
            event_type="comment_added",
            actor="test_poller",
        )

        # Команда на старый ПК успешно завершилась
        cmd.status = "succeeded"
        cmd.completed_at = dt.datetime.now(dt.timezone.utc)
        await db.commit()

        # При следующем шаге advance сверяет команду, обнаруживает дрейф цели и ставит запуск на паузу
        res = await orchestrator.advance(
            run_id=run.id,
            task=task_new,
            comments=[{"Editor": "Заявитель", "Comments": "Мой новый ПК NTEMW0999"}],
            actor="test_poller",
        )

        assert res.run.state == TicketRunState.PAUSED.value
        assert res.run.pause_reason == "target_changed_during_execution"


@pytest.mark.asyncio
async def test_operator_facts_not_collapsed_and_selects_latest_valid(custom_fact_registry):
    """Проверяет сохранение цепочки фактов оператора и выбор последнего валидного неистекшего."""
    now = dt.datetime.now(dt.timezone.utc)
    obs1 = FactObservation(
        key="device.pc_name",
        value="NTEMW0001",
        state=FactState.VALID,
        source=FactSource.OPERATOR,
        source_ref="operator:session1",
        observed_at=(now - dt.timedelta(minutes=10)).isoformat(),
    )
    obs2 = FactObservation(
        key="device.pc_name",
        value="NTEMW0002",
        state=FactState.VALID,
        source=FactSource.OPERATOR,
        source_ref="operator:session1",
        observed_at=(now - dt.timedelta(minutes=5)).isoformat(),
    )
    obs3 = FactObservation(
        key="device.pc_name",
        value="INVALID_PC_STRING",
        state=FactState.INVALID,
        source=FactSource.OPERATOR,
        source_ref="operator:session1",
        observed_at=now.isoformat(),
    )

    bag = merge_observations([obs1, obs2, obs3], registry=custom_fact_registry)
    fact = bag.facts.get("device.pc_name")
    assert fact is not None
    assert fact.state == FactState.VALID
    # Должен быть выбран NTEMW0002 (последний валидный), а не испорчен строкой obs3
    assert fact.value == "NTEMW0002"
    # Все 3 наблюдения сохранены в истории
    assert len(fact.observations) == 3


@pytest.mark.asyncio
async def test_ticket_fact_store_strictly_monotonic_timestamps():
    """Проверяет строго монотонное возрастание времени observed_at при добавлении фактов."""
    async with AsyncSessionLocal() as db:
        run = await TicketRunService(db).start_manual(
            task_id=98704,
            status_id=31,
            allowed_status_ids={31},
            actor="operator:test",
            trigger_key="manual:98704:monotonic",
        )
        store = TicketFactStore(db)

        # Передаем пачку фактов с одинаковым или убывающим временем
        same_time = "2026-09-08T12:00:00.000000+00:00"
        observations = [
            FactObservation(
                key=f"fact_{i}",
                value=f"val_{i}",
                state=FactState.VALID,
                source=FactSource.OPERATOR,
                source_ref="test",
                observed_at=same_time,
            )
            for i in range(5)
        ]
        stored_rows = await store.append(run.id, observations)
        await db.commit()

        loaded = await store.load(run.id)
        assert len(loaded) == 5

        # Проверяем строгое возрастание
        for i in range(len(loaded) - 1):
            t1 = dt.datetime.fromisoformat(loaded[i].observed_at)
            t2 = dt.datetime.fromisoformat(loaded[i + 1].observed_at)
            assert t1 < t2, f"Timestamps must strictly increase: {t1} vs {t2}"


@pytest.mark.asyncio
async def test_manual_run_with_active_command_is_resumable():
    """Проверяет, что manual-запуск с активной командой попадает в list_resumable."""
    async with AsyncSessionLocal() as db:
        run_service = TicketRunService(db)
        run = await run_service.start_manual(
            task_id=98705,
            status_id=31,
            allowed_status_ids={31},
            actor="operator:test",
            trigger_key="manual:98705:resumable",
        )

        # Пока команд нет и статус не running, запуск не должен быть в resumable
        resumable_initial = await run_service.list_resumable()
        assert run.id not in [r.id for r in resumable_initial]

        # Создаем активную команду для запуска
        decision = await DecisionJournalService(db).record_operational(
            task_id=98705,
            ticket_run_id=run.id,
            action="diagnose_host",
            target={"task_id": 98705, "host": "PC-98705"},
            parameters={"host": "PC-98705"},
            actor="operator:test",
        )
        cmd, _ = await CommandService(db).create(
            action="diagnose_host",
            target={"task_id": 98705, "host": "PC-98705"},
            parameters={"host": "PC-98705"},
            idempotency_key="cmd-resumable-98705",
            initiator="operator:test",
            source="web",
            priority=5,
            ticket_run_id=run.id,
            decision_id=decision.id,
            decision_version=decision.version,
        )

        resumable_after_cmd = await run_service.list_resumable()
        assert run.id in [r.id for r in resumable_after_cmd]
