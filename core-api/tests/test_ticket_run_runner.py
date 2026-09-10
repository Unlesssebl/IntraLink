"""Tests for TicketRunRunner facade delegating to TicketRunOrchestrator."""

from __future__ import annotations

import datetime as dt
import uuid
import pytest
from sqlalchemy import select

from app.database.db import (
    AsyncSessionLocal,
    AutopilotScenario,
    CommandRecord,
    ResolutionPolicy,
    ResponseTemplate,
    SystemSetting,
    TicketRun,
)
from app.services.ticket_run_runner import TicketRunRunner
from app.services.ticket_runs import TicketRunService, TicketRunState


async def seed_printer_policies(db) -> None:
    templates = [
        ResponseTemplate(
            key="printer_ip_clarify",
            version=1,
            name="Уточнение IP принтера",
            template_text="Уточните, пожалуйста, IP-адрес принтера.",
            required_variables=[],
            is_active=True,
            created_by="test",
        ),
        ResponseTemplate(
            key="install_printer_proposed",
            version=1,
            name="Установка принтера",
            template_text="Принтер устанавливается на рабочую станцию.",
            required_variables=[],
            is_active=True,
            created_by="test",
        ),
        ResponseTemplate(
            key="resolved_standard",
            version=1,
            name="Заявка выполнена",
            template_text="Добрый день! Принтер успешно установлен.",
            required_variables=[],
            is_active=True,
            created_by="test",
        ),
    ]
    for tmpl in templates:
        existing = await db.scalar(
            select(ResponseTemplate).where(
                ResponseTemplate.key == tmpl.key,
                ResponseTemplate.version == tmpl.version,
            )
        )
        if not existing:
            db.add(tmpl)
    await db.flush()

    tmpl_map = {}
    for t in (await db.scalars(select(ResponseTemplate))).all():
        tmpl_map[t.key] = t.id

    policies = [
        ResolutionPolicy(
            outcome_key="printer_ip_clarify",
            version=1,
            outcome_kind="clarification",
            template_id=tmpl_map["printer_ip_clarify"],
            target_status_id=35,
            status_name="Требует уточнения",
            expenses=5,
            action_id=None,
            risk_level=0,
            requires_approval=False,
            is_active=True,
            created_by="test",
        ),
        ResolutionPolicy(
            outcome_key="install_printer_proposed",
            version=1,
            outcome_kind="action",
            template_id=tmpl_map["install_printer_proposed"],
            target_status_id=None,
            status_name=None,
            expenses=10,
            action_id="install_printer",
            risk_level=1,
            requires_approval=True,
            is_active=True,
            created_by="test",
        ),
        ResolutionPolicy(
            outcome_key="resolved_standard",
            version=1,
            outcome_kind="resolution",
            template_id=tmpl_map["resolved_standard"],
            target_status_id=29,
            status_name="Решена",
            expenses=10,
            action_id=None,
            risk_level=0,
            requires_approval=False,
            is_active=True,
            created_by="test",
        ),
    ]
    for pol in policies:
        existing = await db.scalar(
            select(ResolutionPolicy).where(
                ResolutionPolicy.outcome_key == pol.outcome_key,
                ResolutionPolicy.version == pol.version,
            )
        )
        if not existing:
            db.add(pol)

    existing_setting = await db.scalar(select(SystemSetting).where(SystemSetting.key == "service_account_config"))
    if not existing_setting:
        db.add(
            SystemSetting(
                key="service_account_config",
                value_json={"login": "assistant", "encrypted_password": "test", "user_id": 10001},
                is_encrypted=True,
            )
        )

    existing_scenario = await db.scalar(
        select(AutopilotScenario).where(
            AutopilotScenario.service_id == 19,
            AutopilotScenario.scenario_key == "install_printer",
        )
    )
    if not existing_scenario:
        db.add(
            AutopilotScenario(
                service_id=19,
                scenario_key="install_printer",
                enabled=True,
                rollout_mode="active",
                config_json={},
                updated_by="test",
            )
        )
    else:
        existing_scenario.enabled = True
        existing_scenario.rollout_mode = "active"

    await db.commit()


def test_extract_printer_parameters_from_custom_fields():
    task = {
        "Name": "Заявка на печать",
        "Description": "Установить принтер",
        "CustomFields": [
            {"CustomFieldId": 1112, "Value": "WS-IT-042"},
            {"CustomFieldId": 1103, "Value": "192.168.10.150"},
        ],
    }
    pc_name, printer_ip = TicketRunRunner.extract_printer_parameters(task)
    assert pc_name == "WS-IT-042"
    assert printer_ip == "192.168.10.150"


def test_extract_printer_parameters_from_text_fallback():
    task = {
        "Name": "Подключить принтер Kyocera на PC-081",
        "Description": "Сетевой адрес принтера 10.20.30.40 в бухгалтерии",
        "CustomFields": [],
    }
    pc_name, printer_ip = TicketRunRunner.extract_printer_parameters(task)
    assert pc_name == "PC081"
    assert printer_ip == "10.20.30.40"


def test_is_supported_printer_installation():
    valid_task = {
        "Name": "Установка принтера HP LaserJet",
        "Description": "Прошу установить МФУ в отдел кадров",
    }
    assert TicketRunRunner.is_supported_printer_installation(valid_task, "PC-01", "10.0.0.1") is True

    irrelevant_task = {
        "Name": "Не открывается Excel",
        "Description": "Ошибка формулы ВПР",
    }
    assert TicketRunRunner.is_supported_printer_installation(irrelevant_task, "", "") is False


@pytest.mark.asyncio
async def test_advance_ignores_completed_or_paused_run():
    async with AsyncSessionLocal() as db:
        for tid in (94001, 94002):
            existing = (await db.execute(select(TicketRun).where(TicketRun.task_id == tid))).scalars().all()
            for r in existing:
                await db.delete(r)
        await db.commit()

        completed_run = TicketRun(
            id=uuid.uuid4(),
            task_id=94001,
            mode="autopilot",
            state=TicketRunState.COMPLETED.value,
            trigger_kind="ticket_created",
            trigger_key="ticket:94001:created",
            completed_at=dt.datetime.now(dt.timezone.utc),
            created_by="test",
            updated_by="test",
        )
        paused_run = TicketRun(
            id=uuid.uuid4(),
            task_id=94002,
            mode="autopilot",
            state=TicketRunState.PAUSED.value,
            trigger_kind="ticket_created",
            trigger_key="ticket:94002:created",
            pause_reason="manual_review",
            created_by="test",
            updated_by="test",
        )
        db.add_all([completed_run, paused_run])
        await db.commit()

        runner = TicketRunRunner(db)
        res1 = await runner.advance(run_id=completed_run.id, task={"Id": 94001})
        assert res1.state == TicketRunState.COMPLETED.value

        res2 = await runner.advance(run_id=paused_run.id, task={"Id": 94002})
        assert res2.state == TicketRunState.PAUSED.value


@pytest.mark.asyncio
async def test_advance_legacy_rollout_mode_falls_back_to_orchestrator():
    async with AsyncSessionLocal() as db:
        await seed_printer_policies(db)
        existing = (await db.execute(select(TicketRun).where(TicketRun.task_id == 94003))).scalars().all()
        for r in existing:
            await db.delete(r)
        await db.commit()

        scenario = await db.scalar(
            select(AutopilotScenario).where(
                AutopilotScenario.service_id == 19,
                AutopilotScenario.scenario_key == "install_printer",
            )
        )
        scenario.rollout_mode = "legacy"
        await db.commit()

        run = TicketRun(
            id=uuid.uuid4(),
            task_id=94003,
            mode="autopilot",
            state=TicketRunState.RUNNING.value,
            trigger_kind="ticket_created",
            trigger_key="ticket:94003:created",
            scenario_key="install_printer",
            scenario_version=1,
            trigger_snapshot_json={"scenario_key": "install_printer"},
            created_by="test",
            updated_by="test",
        )
        db.add(run)
        await db.commit()

        task = {
            "Id": 94003,
            "ServiceId": 19,
            "Name": "Подключить принтер Kyocera",
            "Description": "Установить принтер",
            "CustomFields": [
                {"CustomFieldId": 1112, "Value": "WS-01"},
                {"CustomFieldId": 1103, "Value": "10.0.0.50"},
            ],
        }

        runner = TicketRunRunner(db)
        res = await runner.advance(run_id=run.id, task=task)
        assert res.state in (TicketRunState.RUNNING.value, TicketRunState.WAITING_APPROVAL.value)
        assert res.current_step in ("execute:install_printer", "request_clarification")


@pytest.mark.asyncio
async def test_advance_printer_installation_creates_command():
    """Тест создания команды install_printer через фасад TicketRunRunner."""
    async with AsyncSessionLocal() as db:
        await seed_printer_policies(db)
        task_id = 94004
        existing = (await db.execute(select(TicketRun).where(TicketRun.task_id == task_id))).scalars().all()
        for r in existing:
            await db.delete(r)
        await db.commit()

        run = TicketRun(
            id=uuid.uuid4(),
            task_id=task_id,
            mode="autopilot",
            state=TicketRunState.RUNNING.value,
            trigger_kind="ticket_created",
            trigger_key=f"ticket:{task_id}:created",
            scenario_key="install_printer",
            scenario_version=1,
            trigger_snapshot_json={"scenario_key": "install_printer"},
            created_by="test",
            updated_by="test",
        )
        db.add(run)
        await db.commit()

        task = {
            "Id": task_id,
            "ServiceId": 19,
            "Name": "Установить принтер HP LaserJet",
            "Description": "Подключение принтера HP LaserJet 2055 на PC-TEST-99 IP 172.16.20.10",
            "PrinterName": "HP LaserJet 2055",
            "CustomFields": [
                {"CustomFieldId": 1112, "Value": "PC-TEST-99"},
                {"CustomFieldId": 1103, "Value": "172.16.20.10"},
            ],
        }

        runner = TicketRunRunner(db)
        advanced = await runner.advance(run_id=run.id, task=task)
        assert advanced.state in (
            TicketRunState.RUNNING.value,
            TicketRunState.WAITING_APPROVAL.value,
            TicketRunState.WAITING_ANSWER.value,
        )

        cmd = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
            )
        )
        assert cmd is not None
