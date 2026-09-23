"""End-to-End (E2E) tests for Milestone 5: Autopilot internal comments full lifecycle acceptance.

Tested against real PostgreSQL 16 (intraservice_test on port 5434).
Zero SQLite usage invariant strictly preserved.
"""

from __future__ import annotations

import json
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
from app.services.ticket_runs import TicketRunState


@pytest.fixture(autouse=True)
async def setup_e2e_environment():
    """Ensure clean baseline settings for E2E acceptance tests."""
    with patch("app.services.scenario_orchestrator.collect_ticket_observations", AsyncMock(return_value=[])), \
         patch("app.services.command_delivery.CommandDeliveryService.deliver_run_failure", AsyncMock(return_value=True)), \
         patch("app.services.intraservice.update_task_full", AsyncMock(return_value=True)), \
         patch("app.services.intraservice.get_task_lifetime", AsyncMock(return_value=[])):
        async with AsyncSessionLocal() as db:
            # Baseline global autopilot settings
            setting = await db.scalar(select(AutopilotSetting).where(AutopilotSetting.key == "global"))
            if setting is None:
                setting = AutopilotSetting(
                    key="global",
                    enabled=True,
                    internal_comments_enabled=True,
                    internal_comments_depth="applied",
                    version=1,
                    updated_by="e2e_test",
                )
                db.add(setting)
            else:
                setting.enabled = True
                setting.internal_comments_enabled = True
                setting.internal_comments_depth = "applied"

            # Baseline scenario for install_printer
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
                    updated_by="e2e_test",
                )
                db.add(scenario)
            else:
                scenario.enabled = True
                scenario.rollout_mode = "active"
                scenario.config_json = {}

            await db.commit()
        yield


@pytest.mark.asyncio
async def test_e2e_full_lifecycle_success_with_internal_comments():
    """Сквозной E2E-тест успешного жизненного цикла: on_start -> execute command -> on_complete."""
    task_id = 200201
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
        "Name": "Установить принтер в бухгалтерии",
        "Description": "Подключите принтер Kyocera на ПК NTEMW0047, IP: 10.244.12.50",
    }

    mock_add_comment = AsyncMock(return_value=True)

    with patch("app.services.intraservice.add_task_comment", mock_add_comment), \
         patch.object(TicketRunOrchestrator, "_resolve_service_auth", AsyncMock(return_value="mock_auth_token")):

        # Шаг 1: Первый advance - запуск цикла (должен сгенерировать on_start скрытый комментарий)
        async with AsyncSessionLocal() as db:
            orchestrator = TicketRunOrchestrator(db)
            res1 = await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )
            assert res1 is not None

        # Проверяем, что отправлен on_start комментарий с флагом is_private=True
        start_calls = [
            c for c in mock_add_comment.call_args_list
            if "[IntraLink Autopilot: Старт]" in c[1]["comment"]
        ]
        assert len(start_calls) == 1
        assert start_calls[0][1]["task_id"] == task_id
        assert start_calls[0][1]["is_private"] is True
        assert "«Установка сетевого принтера»" in start_calls[0][1]["comment"]

        # Имитируем успешное завершение выполнения: команда apply_triage со статусом succeeded
        async with AsyncSessionLocal() as db:
            cmd = await db.scalar(select(CommandRecord).where(CommandRecord.ticket_run_id == run_id))
            if cmd is None:
                cmd = CommandRecord(
                    action="apply_triage",
                    executor="worker",
                    request_hash="req_hash_triage_ok",
                    target_json={"task_id": task_id},
                    params_json={"status_id": 29, "comment": "Принтер успешно установлен"},
                    idempotency_key=f"{run_id}:2:apply_triage",
                    initiator="test",
                    source="test",
                    status="succeeded",
                    result_json={"success": True},
                    ticket_run_id=run_id,
                )
                db.add(cmd)
            else:
                cmd.action = "apply_triage"
                cmd.status = "succeeded"
                cmd.version += 1
            await db.commit()

        # Шаг 2: Второй advance - сверка apply_triage и переход в завершение (on_complete)
        mock_add_comment.reset_mock()
        async with AsyncSessionLocal() as db:
            orchestrator = TicketRunOrchestrator(db)
            res2 = await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )
            assert res2 is not None

        # Проверяем, что отправлен on_complete комментарий с флагом is_private=True
        complete_calls = [
            c for c in mock_add_comment.call_args_list
            if "[IntraLink Autopilot: Успех]" in c[1]["comment"]
        ]
        assert len(complete_calls) == 1
        assert complete_calls[0][1]["task_id"] == task_id
        assert complete_calls[0][1]["is_private"] is True
        assert "успешно завершён" in complete_calls[0][1]["comment"]

        # Проверяем сохраненные события TicketRunEvent в БД
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
            triggers = [e.details_json.get("trigger") for e in events if e.details_json]
            assert "on_start" in triggers
            assert "on_complete" in triggers


@pytest.mark.asyncio
async def test_e2e_lifecycle_pause_clarification():
    """Сквозной E2E-тест перехода в паузу/уточнение: on_pause_or_error при сбое команды."""
    task_id = 200202
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

        # Имитируем сбой выполнения сетевой команды
        cmd = CommandRecord(
            action="install_printer",
            executor="worker",
            request_hash="req_hash_fail",
            target_json={"pc_name": "NTEMW0099"},
            params_json={"printer_ip": "10.244.12.99"},
            idempotency_key=f"{run.id}:1:install_printer",
            initiator="test",
            source="test",
            status="failed",
            error_message="SMB Port 445 filtered or PC is offline",
            ticket_run_id=run.id,
        )
        db.add(cmd)
        await db.commit()
        run_id = run.id

    task_payload = {
        "Id": task_id,
        "ServiceId": 40,
        "Name": "Сбой подключения принтера",
        "Description": "NTEMW0099 10.244.12.99",
    }

    mock_add_comment = AsyncMock(return_value=True)

    with patch("app.services.intraservice.add_task_comment", mock_add_comment), \
         patch.object(TicketRunOrchestrator, "_resolve_service_auth", AsyncMock(return_value="mock_auth_token")):

        async with AsyncSessionLocal() as db:
            orchestrator = TicketRunOrchestrator(db)
            res = await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )
            assert res is not None

        # Проверяем, что отправлен on_pause_or_error комментарий
        pause_calls = [
            c for c in mock_add_comment.call_args_list
            if "[IntraLink Autopilot: Пауза]" in c[1]["comment"]
        ]
        assert len(pause_calls) == 1
        assert pause_calls[0][1]["task_id"] == task_id
        assert pause_calls[0][1]["is_private"] is True
        assert "SMB Port 445 filtered" in pause_calls[0][1]["comment"]

        # Проверяем запись события on_pause_or_error
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
            triggers = [e.details_json.get("trigger") for e in events if e.details_json]
            assert "on_pause_or_error" in triggers


@pytest.mark.asyncio
async def test_e2e_technical_depth_and_dlp_masking():
    """Сквозной E2E-тест технического дампа: форматирование JSON Trace и 100% маскировка паролей."""
    task_id = 200203
    async with AsyncSessionLocal() as db:
        scenario = await db.scalar(
            select(AutopilotScenario).where(
                AutopilotScenario.service_id == 40,
                AutopilotScenario.scenario_key == "install_printer",
            )
        )
        assert scenario is not None
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
        await db.flush()

        # Создаем команду с секретами в тексте ошибки
        cmd = CommandRecord(
            action="install_printer",
            executor="worker",
            request_hash="req_hash_secret_error",
            target_json={"pc_name": "NTEMW0010"},
            params_json={"printer_ip": "10.244.12.10"},
            idempotency_key=f"{run.id}:1:install_printer",
            initiator="test",
            source="test",
            status="failed",
            error_message="Auth error: failed for admin_password='SuperSecretPassword123!' and token Bearer secret_jwt_token_here",
            ticket_run_id=run.id,
        )
        db.add(cmd)
        await db.commit()
        run_id = run.id

    task_payload = {
        "Id": task_id,
        "ServiceId": 40,
        "Name": "Установка с учетными данными",
        "Description": "NTEMW0010 10.244.12.10",
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

    # Находим комментарий с ошибкой on_pause_or_error
    pause_trace = next(c for c in trace_calls if '"event": "on_pause_or_error"' in c[1]["comment"])
    comment_body = pause_trace[1]["comment"]

    # Проверка Markdown форматирования
    assert "[IntraLink Autopilot: Trace]" in comment_body
    assert "```json" in comment_body
    assert "```" in comment_body

    # Проверка DLP маскировки: ни один секрет не должен быть в открытом виде
    assert "SuperSecretPassword123!" not in comment_body
    assert "secret_jwt_token_here" not in comment_body
    assert "***REDACTED***" in comment_body

    # Извлечение и парсинг JSON из блока кода отчета
    json_part = comment_body.split("```json\n")[1].split("\n```")[0]
    parsed_json = json.loads(json_part)
    assert parsed_json["task_id"] == task_id
    assert parsed_json["event"] == "on_pause_or_error"
    assert "***REDACTED***" in parsed_json["error_detail"]


@pytest.mark.asyncio
async def test_e2e_shadow_mode_no_comments():
    """Сквозной E2E-тест режима shadow: комментарии в IntraService не отправляются."""
    task_id = 200204
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
        run_id = run.id

    task_payload = {
        "Id": task_id,
        "ServiceId": 40,
        "Name": "Тест shadow режима E2E",
        "Description": "NTEMW0099 10.244.12.50",
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

    # В режиме shadow вызовы во внешнюю систему полностью отсутствуют
    assert mock_add_comment.call_count == 0

    # Проверяем, что в БД нет событий отправки
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
        assert len(events) == 0


@pytest.mark.asyncio
async def test_e2e_canary_mode_behavior():
    """Сквозной E2E-тест режима canary: изоляция отправки по процентному порогу."""
    task_id = 200205
    async with AsyncSessionLocal() as db:
        scenario = await db.scalar(
            select(AutopilotScenario).where(
                AutopilotScenario.service_id == 40,
                AutopilotScenario.scenario_key == "install_printer",
            )
        )
        assert scenario is not None
        scenario.rollout_mode = "canary"
        scenario.config_json = {"canary_percent": 0}  # Ни один тикет не попадает
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
        run_id = run.id

    task_payload = {
        "Id": task_id,
        "ServiceId": 40,
        "Name": "Тест canary 0%",
        "Description": "NTEMW0099 10.244.12.50",
    }

    mock_add_comment = AsyncMock(return_value=True)

    with patch("app.services.intraservice.add_task_comment", mock_add_comment), \
         patch.object(TicketRunOrchestrator, "_resolve_service_auth", AsyncMock(return_value="mock_auth_token")):

        # 1. При canary_percent=0 комментарий не отправляется
        async with AsyncSessionLocal() as db:
            orchestrator = TicketRunOrchestrator(db)
            await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )
        assert mock_add_comment.call_count == 0

        # 2. Переводим в 100% canary - комментарий успешно отправляется
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
        assert mock_add_comment.call_args[1]["is_private"] is True


@pytest.mark.asyncio
async def test_e2e_network_failure_resilience():
    """Сквозной E2E-тест отказоустойчивости (Fault Tolerance): сбой IntraService не ломает бизнес-процесс."""
    task_id = 200206
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
        "Name": "Установить принтер в бухгалтерии",
        "Description": "Подключите принтер Kyocera на ПК NTEMW0047, IP: 10.244.12.50",
    }

    # Имитируем сетевую ошибку 500 / Timeout от IntraService
    mock_failing_add_comment = AsyncMock(side_effect=RuntimeError("IntraService 500 Internal Server Error"))

    with patch("app.services.intraservice.add_task_comment", mock_failing_add_comment), \
         patch.object(TicketRunOrchestrator, "_resolve_service_auth", AsyncMock(return_value="mock_auth_token")):

        async with AsyncSessionLocal() as db:
            orchestrator = TicketRunOrchestrator(db)
            # advance должен завершиться без выброса исключения (Fault Tolerance)
            result = await orchestrator.advance(
                run_id=run_id,
                task=task_payload,
                comments=[],
                actor="test_runner",
            )
            assert result is not None
            assert result.run.id == run_id

        # Проверяем, что событие попытки отправки зафиксировано с delivered=False
        async with AsyncSessionLocal() as db:
            event = await db.scalar(
                select(TicketRunEvent).where(
                    TicketRunEvent.ticket_run_id == run_id,
                    TicketRunEvent.event_type == "internal_comment_sent",
                )
            )
            assert event is not None
            assert event.details_json["delivered"] is False
