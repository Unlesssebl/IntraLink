"""Configuration script for Stage 5: Autopilot scenario and policies setup."""

from __future__ import annotations

import asyncio
from sqlalchemy import select

from app.database.db import (
    ActionPolicyRecord,
    AsyncSessionLocal,
    AutopilotScenario,
)
from app.services.command_service import CommandService
from app.services.ticket_runs import TicketRunService


async def main() -> None:
    async with AsyncSessionLocal() as db:
        # 1. Создать или обновить autopilot_scenarios для сервиса 53
        scenario = await db.scalar(
            select(AutopilotScenario).where(
                AutopilotScenario.service_id == 53,
                AutopilotScenario.scenario_key == "create_user",
            )
        )
        if scenario is None:
            scenario = AutopilotScenario(
                service_id=53,
                scenario_key="create_user",
                enabled=True,
                rollout_mode="active",
                config_json={},
                updated_by="belikov.a",
            )
            db.add(scenario)
            print("[Stage 5] AutopilotScenario создан: service_id=53, key='create_user', enabled=True, rollout_mode='active'")
        else:
            scenario.enabled = True
            scenario.rollout_mode = "active"
            scenario.config_json = {}
            scenario.updated_by = "belikov.a"
            print("[Stage 5] AutopilotScenario обновлен: service_id=53, key='create_user', enabled=True, rollout_mode='active'")
        await db.commit()

        # 2. Настроить action_policies через CommandService с аудитом:
        # - create_user = confirm для первого запуска
        # - apply_triage = auto
        cmd_service = CommandService(db)
        await cmd_service.set_policy(
            action="create_user",
            mode="confirm",
            actor="belikov.a",
            reason="Первый запуск с подтверждением оператора для безопасной проверки создания учетных записей",
        )
        print("[Stage 5] ActionPolicy установлена: create_user = confirm (автор: belikov.a)")

        await cmd_service.set_policy(
            action="apply_triage",
            mode="auto",
            actor="belikov.a",
            reason="Автоматическая публикация запроса данных и финализация",
        )
        print("[Stage 5] ActionPolicy установлена: apply_triage = auto (автор: belikov.a)")

        # 3. Включить глобальный автопилот через TicketRunService.set_global_enabled
        run_service = TicketRunService(db)
        current_setting = await run_service.get_global_setting()
        expected_version = current_setting.version
        updated_setting = await run_service.set_global_enabled(
            enabled=True,
            actor="belikov.a",
            reason="Безопасный запуск автопилота для создания учетных записей (сценарий create_user)",
            expected_version=expected_version,
        )
        print(
            f"[Stage 5] Глобальный автопилот включен: enabled={updated_setting.enabled}, "
            f"версия={updated_setting.version}, автор={updated_setting.updated_by}"
        )


if __name__ == "__main__":
    asyncio.run(main())
