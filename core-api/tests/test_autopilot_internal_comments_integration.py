"""Integration tests for Milestone 3: Autopilot internal comments orchestration and safety guards."""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.database.db import (
    AsyncSessionLocal,
    AutopilotScenario,
    AutopilotSetting,
    CommandRecord,
    TicketRun,
    TicketRunEvent,
)
from app.services.scenario_orchestrator import TicketRunOrchestrator
from app.services.ticket_runs import TicketRunService, TicketRunState


@pytest.fixture(autouse=True)
async def setup_test_environment():
    """Ensure clean baseline settings for each test."""
    with patch("app.services.scenario_orchestrator.collect_ticket_observations", AsyncMock(return_value=[])), \
         patch("app.services.command_delivery.CommandDeliveryService.deliver_run_failure", AsyncMock(return_value=True)):
        async with AsyncSessionLocal() as db:
            # Убедимся, что глобальные настройки активны
            setting = await db.scalar(select(AutopilotSetting).where(AutopilotSetting.key == "global"))
            if setting is None:
                setting = AutopilotSetting(
                    key="global",
                    enabled=True,
                    internal_comments_enabled=True,
                    internal_comments_depth="applied",
                    version=1,
                    updated_by="test",
                )
                db.add(setting)
            else:
                setting.enabled = True
                setting.internal_comments_enabled = True
                setting.internal_comments_depth = "applied"

            # Убедимся, что тестовый сценарий существует
            scenario = await db.scalar(
                select(AutopilotScenario).where(
                    AutopilotScenario.service_id == 40,
                    AutopilotScenario.scenario_key == "install_printer",
                )
            )
            if scenario is None:
                scenario = AutopilotScenario(
                    service_id=40,
                    scenario_key="install_printer",
                    version=1,
                    enabled=True,
                    rollout_mode="active",
                    config_json={},
                    updated_by="test",
                )
                db.add(scenario)
            else:
                scenario.enabled = True
                scenario.rollout_mode = "active"
                scenario.config_json = {}

            await db.commit()
        yield


@pytest.mark.asyncio
async def test_on_start_comment_dispatched_and_idempotent():
    """Проверяем успешную отправку on_start и защиту от повторной отправки."""
    task_id = 200101
    async with AsyncSessionLocal() as db:
        run = TicketRun(
            task_id=task_id,
            mode="autopilot",
            state=TicketRunState.RUNNING.value,
            scenario_key="install_printer",
            scenario_version=1,
            trigger_kind="test",
            trigger_key=f"test:{task_id}",
            created_by="test",
            updated_by="test",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id

    task_payload = {
        "Id": task_id,
        "ServiceId": 40,
        "Name": "Установить принтер",
        "Description": "Подключите принтер Kyocera на ПК NTEMW0047, IP: 10.244.12.50",
    }

    mock_add_comment = AsyncMock(return_value=True)

    with patch("app.services.intraservice.add_task_comment", mock_add_comment), \
         patch.object(TicketRunOrchestrator, "_resolve_service_auth", AsyncMock(return_value="mock_auth_token")):

        async with AsyncSessionLocal() as db:
            orchestrator = TicketRunOrchestrator(db)
            res1 = await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )

        start_calls = [
            c for c in mock_add_comment.call_args_list
            if "[IntraLink Autopilot: Старт]" in c[1]["comment"]
        ]
        assert len(start_calls) == 1
        call_args = start_calls[0][1]
        assert call_args["task_id"] == task_id
        assert call_args["is_private"] is True
        assert "[IntraLink Autopilot: Старт]" in call_args["comment"]

        # Проверяем запись события TicketRunEvent
        async with AsyncSessionLocal() as db:
            events = list(
                (
                    await db.scalars(
                        select(TicketRunEvent).where(
                            TicketRunEvent.ticket_run_id == run_id,
                            TicketRunEvent.event_type == "internal_comment_sent",
                        )
                    )
                ).all()
            )
            assert len(events) >= 1
            start_event = next(e for e in events if (e.details_json or {}).get("trigger") == "on_start")
            assert start_event.details_json["delivered"] is True
            assert start_event.details_json["depth"] == "applied"

        # 2-й проход: повторный advance не должен дублировать отправку on_start
        mock_add_comment.reset_mock()
        async with AsyncSessionLocal() as db:
            orchestrator = TicketRunOrchestrator(db)
            res2 = await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )

        # Вызов для on_start не должен повторяться
        start_calls_again = [
            c for c in mock_add_comment.call_args_list
            if "[IntraLink Autopilot: Старт]" in c[1]["comment"]
        ]
        assert len(start_calls_again) == 0


@pytest.mark.asyncio
async def test_shadow_mode_blocks_internal_comments():
    """В режиме shadow отправка комментариев в IntraService полностью блокируется."""
    task_id = 200102
    async with AsyncSessionLocal() as db:
        scenario = await db.scalar(
            select(AutopilotScenario).where(
                AutopilotScenario.service_id == 40,
                AutopilotScenario.scenario_key == "install_printer",
            )
        )
        assert scenario is not None
        scenario.rollout_mode = "shadow"
        await db.commit()

        run = TicketRun(
            task_id=task_id,
            mode="autopilot",
            state=TicketRunState.RUNNING.value,
            scenario_key="install_printer",
            scenario_version=1,
            trigger_kind="test",
            trigger_key=f"test:{task_id}",
            created_by="test",
            updated_by="test",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id

    task_payload = {
        "Id": task_id,
        "ServiceId": 40,
        "Name": "Установить принтер в shadow",
        "Description": "Проверка shadow режима NTEMW0099",
    }

    mock_add_comment = AsyncMock(return_value=True)

    with patch("app.services.intraservice.add_task_comment", mock_add_comment), \
         patch.object(TicketRunOrchestrator, "_resolve_service_auth", AsyncMock(return_value="mock_auth_token")):

        async with AsyncSessionLocal() as db:
            orchestrator = TicketRunOrchestrator(db)
            await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )

    # В shadow режиме вызовов add_task_comment быть НЕ ДОЛЖНО
    assert mock_add_comment.call_count == 0


@pytest.mark.asyncio
async def test_canary_mode_filtering():
    """В режиме canary комментарии уходят только для выбранного бакета."""
    task_id = 200103
    async with AsyncSessionLocal() as db:
        scenario = await db.scalar(
            select(AutopilotScenario).where(
                AutopilotScenario.service_id == 40,
                AutopilotScenario.scenario_key == "install_printer",
            )
        )
        assert scenario is not None
        scenario.rollout_mode = "canary"
        scenario.config_json = {"canary_percent": 0}  # 0% - никто не попадает
        await db.commit()

        run = TicketRun(
            task_id=task_id,
            mode="autopilot",
            state=TicketRunState.RUNNING.value,
            scenario_key="install_printer",
            scenario_version=1,
            trigger_kind="test",
            trigger_key=f"test:{task_id}",
            created_by="test",
            updated_by="test",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id

    task_payload = {
        "Id": task_id,
        "ServiceId": 40,
        "Name": "Установка canary 0%",
        "Description": "NTEMW0099 10.244.12.1",
    }

    mock_add_comment = AsyncMock(return_value=True)

    with patch("app.services.intraservice.add_task_comment", mock_add_comment), \
         patch.object(TicketRunOrchestrator, "_resolve_service_auth", AsyncMock(return_value="mock_auth_token")):

        # 1. При canary_percent=0 комментарий блокируется
        async with AsyncSessionLocal() as db:
            orchestrator = TicketRunOrchestrator(db)
            await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )
        assert mock_add_comment.call_count == 0

        # 2. Обновляем canary_percent до 100%
        async with AsyncSessionLocal() as db:
            sc = await db.scalar(
                select(AutopilotScenario).where(
                    AutopilotScenario.service_id == 40,
                    AutopilotScenario.scenario_key == "install_printer",
                )
            )
            sc.config_json = {"canary_percent": 100}
            await db.commit()

            orchestrator = TicketRunOrchestrator(db)
            await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )
        assert mock_add_comment.call_count == 1


@pytest.mark.asyncio
async def test_scenario_override_depth_technical():
    """Сценарий переопределяет глобальную настройку depth='applied' на 'technical'."""
    task_id = 200104
    async with AsyncSessionLocal() as db:
        scenario = await db.scalar(
            select(AutopilotScenario).where(
                AutopilotScenario.service_id == 40,
                AutopilotScenario.scenario_key == "install_printer",
            )
        )
        assert scenario is not None
        scenario.rollout_mode = "active"
        scenario.config_json = {"internal_comments_depth": "technical"}
        await db.commit()

        run = TicketRun(
            task_id=task_id,
            mode="autopilot",
            state=TicketRunState.RUNNING.value,
            scenario_key="install_printer",
            scenario_version=1,
            trigger_kind="test",
            trigger_key=f"test:{task_id}",
            created_by="test",
            updated_by="test",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id

    task_payload = {
        "Id": task_id,
        "ServiceId": 40,
        "Name": "Технический дамп",
        "Description": "Проверка оверрайда глубины NTEMW0047 10.244.12.10",
    }

    mock_add_comment = AsyncMock(return_value=True)

    with patch("app.services.intraservice.add_task_comment", mock_add_comment), \
         patch.object(TicketRunOrchestrator, "_resolve_service_auth", AsyncMock(return_value="mock_auth_token")):

        async with AsyncSessionLocal() as db:
            orchestrator = TicketRunOrchestrator(db)
            await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )

    trace_calls = [
        c for c in mock_add_comment.call_args_list
        if "[IntraLink Autopilot: Trace]" in c[1]["comment"]
    ]
    assert len(trace_calls) >= 1
    start_trace = next(c for c in trace_calls if '"event": "on_start"' in c[1]["comment"])
    comment = start_trace[1]["comment"]
    assert "[IntraLink Autopilot: Trace]" in comment
    assert "```json" in comment
    assert '"event": "on_start"' in comment


@pytest.mark.asyncio
async def test_on_pause_comment_on_command_failure():
    """Проверяем отправку on_pause_or_error при сбое выполнения команды."""
    task_id = 200105
    async with AsyncSessionLocal() as db:
        run = TicketRun(
            task_id=task_id,
            mode="autopilot",
            state=TicketRunState.RUNNING.value,
            scenario_key="install_printer",
            scenario_version=1,
            trigger_kind="test",
            trigger_key=f"test:{task_id}",
            created_by="test",
            updated_by="test",
        )
        db.add(run)
        await db.flush()

        cmd = CommandRecord(
            action="install_printer",
            executor="worker",
            request_hash="mock_req_hash",
            target_json={"pc_name": "NTEMW0047"},
            params_json={"printer_ip": "10.244.12.50"},
            idempotency_key=f"{run.id}:1:install_printer",
            initiator="test",
            source="test",
            status="failed",
            error_message="Host unreachable or port 445 blocked",
            ticket_run_id=run.id,
        )
        db.add(cmd)
        await db.commit()
        run_id = run.id

    task_payload = {
        "Id": task_id,
        "ServiceId": 40,
        "Name": "Сбой установки",
        "Description": "NTEMW0047",
    }

    mock_add_comment = AsyncMock(return_value=True)

    with patch("app.services.intraservice.add_task_comment", mock_add_comment), \
         patch.object(TicketRunOrchestrator, "_resolve_service_auth", AsyncMock(return_value="mock_auth_token")):

        async with AsyncSessionLocal() as db:
            orchestrator = TicketRunOrchestrator(db)
            await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )

    # Проверяем, что был отправлен комментарий о паузе
    pause_calls = [
        c for c in mock_add_comment.call_args_list
        if "[IntraLink Autopilot: Пауза]" in c[1]["comment"]
    ]
    assert len(pause_calls) == 1
    assert "Host unreachable" in pause_calls[0][1]["comment"]


@pytest.mark.asyncio
async def test_fault_tolerance_on_intraservice_network_error():
    """Сетевой сбой отправки комментария в IntraService не роняет advance оркестратора."""
    task_id = 200106
    async with AsyncSessionLocal() as db:
        run = TicketRun(
            task_id=task_id,
            mode="autopilot",
            state=TicketRunState.RUNNING.value,
            scenario_key="install_printer",
            scenario_version=1,
            trigger_kind="test",
            trigger_key=f"test:{task_id}",
            created_by="test",
            updated_by="test",
        )
        db.add(run)
        await db.commit()
        run_id = run.id

    task_payload = {
        "Id": task_id,
        "ServiceId": 40,
        "Name": "Сетевая ошибка IntraService",
        "Description": "NTEMW0047 10.244.12.50",
    }

    mock_failing_comment = AsyncMock(side_effect=RuntimeError("IntraService Gateway Timeout 504"))

    with patch("app.services.intraservice.add_task_comment", mock_failing_comment), \
         patch.object(TicketRunOrchestrator, "_resolve_service_auth", AsyncMock(return_value="mock_auth_token")):

        async with AsyncSessionLocal() as db:
            orchestrator = TicketRunOrchestrator(db)
            # advance НЕ должен упасть с исключением
            res = await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )
            assert res is not None
            assert res.run.id == run_id
