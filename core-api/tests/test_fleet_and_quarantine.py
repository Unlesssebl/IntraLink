"""
Тесты Инкремента 5: Worker Fleet, Routing Quarantine, Capability-based Routing и Отказоустойчивость.
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.database.db import (
    ActionPolicyRecord,
    AsyncSessionLocal,
    CommandApproval,
    CommandAttempt,
    CommandEvent,
    CommandInbox,
    CommandOutbox,
    CommandRecord,
    User,
    init_db,
)
from app.services.command_service import CommandService, resolve_command_stream
from app.services.identity import create_service_credential
from app.main import app
from worker import WindowsExecutionWorker


@pytest_asyncio.fixture(autouse=True)
async def clean_db_state():
    await init_db()
    async with AsyncSessionLocal() as db:
        for model in (
            ActionPolicyRecord,
            CommandApproval,
            CommandAttempt,
            CommandInbox,
            CommandEvent,
            CommandOutbox,
            CommandRecord,
            User,
        ):
            await db.execute(delete(model))
        await db.commit()
    yield


@pytest.mark.asyncio
async def test_resolve_command_stream_and_routing_keys():
    """Проверка разрешения подстримов по префиксам действий."""
    # 1. Active Directory действия
    stream, key = resolve_command_stream("ad_create_user")
    assert stream == "stream:execution_commands:v2:ad"
    assert key == "ad"

    stream, key = resolve_command_stream("user_access")
    assert stream == "stream:execution_commands:v2:ad"
    assert key == "ad"

    # 2. Принтеры
    stream, key = resolve_command_stream("install_printer")
    assert stream == "stream:execution_commands:v2:printer"
    assert key == "printer"

    stream, key = resolve_command_stream("printer_restart_spooler")
    assert stream == "stream:execution_commands:v2:printer"
    assert key == "printer"

    # 3. WinRM
    stream, key = resolve_command_stream("run_winrm_script")
    assert stream == "stream:execution_commands:v2:winrm"
    assert key == "winrm"

    # 4. Неспецифичные действия -> базовый поток
    stream, key = resolve_command_stream("custom_generic_action")
    assert stream == "stream:execution_commands:v2"
    assert key == "default"


@pytest.mark.asyncio
async def test_outbox_records_capability_stream_and_routing_key():
    """Проверка фиксации routing_key и потока в outbox при создании v2 команды."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        # Действие auto-режима
        cmd, _ = await service.create(
            action="diagnose_host",
            target={"host": "PC-TEST-ROUTE"},
            parameters={},
            idempotency_key="test-routing-outbox-key",
            initiator="operator",
            source="test",
            priority=5,
        )
        assert cmd.status == "queued"

        outbox = await db.scalar(
            select(CommandOutbox).where(CommandOutbox.command_id == cmd.id)
        )
        assert outbox is not None
        assert outbox.payload_json.get("routing_key") is not None
        # diagnose_host не начинается с ad_, printer_, winrm_ -> default stream
        assert outbox.stream == "stream:execution_commands:v2"
        assert outbox.payload_json.get("routing_key") == "default"


@pytest.mark.asyncio
async def test_quarantine_command_service_logic():
    """Тест перевода команды в Routing Quarantine через CommandService."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        cmd, _ = await service.create(
            action="diagnose_host",
            target={"host": "PC-QUARANTINE-01"},
            parameters={},
            idempotency_key="test-quarantine-service-key",
            initiator="operator",
            source="test",
            priority=5,
        )
        assert cmd.status == "queued"

        # Отправляем команду в карантин
        quarantined = await service.quarantine(
            cmd.id,
            worker_id="node-win-01",
            missing_capability="vpn_direct_access",
            message_id="1710000000000-0",
            reason="Сегмент сети недоступен с узла node-win-01",
        )

        assert quarantined.status == "needs_review"

        # Проверяем запись события routing_quarantined
        events = list(
            (
                await db.scalars(
                    select(CommandEvent)
                    .where(CommandEvent.command_id == cmd.id)
                    .order_by(CommandEvent.sequence)
                )
            ).all()
        )
        quarantine_event = next(
            (e for e in events if e.event_type == "routing_quarantined"), None
        )
        assert quarantine_event is not None
        assert quarantine_event.details_json["missing_capability"] == "vpn_direct_access"
        assert quarantine_event.details_json["worker_id"] == "node-win-01"
        assert quarantine_event.details_json["message_id"] == "1710000000000-0"

        # Идемпотентность: повторный вызов не ломает команду
        quarantined_again = await service.quarantine(
            cmd.id,
            worker_id="node-win-01",
            missing_capability="vpn_direct_access",
            message_id="1710000000000-0",
        )
        assert quarantined_again.status == "needs_review"


@pytest.mark.asyncio
async def test_quarantine_http_endpoint_with_auth():
    """Тест HTTP эндпоинта POST /api/v2/commands/{id}/quarantine с сервисным токеном."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        cmd, _ = await service.create(
            action="diagnose_host",
            target={"host": "PC-QUARANTINE-HTTP"},
            parameters={},
            idempotency_key="test-quarantine-http-key",
            initiator="operator",
            source="test",
            priority=5,
        )
        command_id = cmd.id

        # Создаем сервисные учетные данные
        _principal, credential, secret = await create_service_credential(
            db,
            subject="service-win-worker",
            display_name="Windows Worker",
            scopes={"command:claim:windows", "command:finish:windows"},
        )
        key_id = credential.key_id

    headers = {
        "X-Service-Key-Id": key_id,
        "X-Service-Secret": secret,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Успешный перевод в карантин
        resp = await client.post(
            f"/api/v2/commands/{command_id}/quarantine",
            headers=headers,
            json={
                "worker_id": "win_node_hq",
                "missing_capability": "ad_tools",
                "message_id": "1720000000000-1",
                "reason": "RSAT не установлен на целевом узле",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "needs_review"
        assert data["quarantined"] is True
        assert data["missing_capability"] == "ad_tools"

        # Без авторизации -> 401
        resp_unauth = await client.post(
            f"/api/v2/commands/{command_id}/quarantine",
            json={
                "worker_id": "win_node_hq",
                "missing_capability": "ad_tools",
            },
        )
        assert resp_unauth.status_code == 401


class FakeRedisFleet:
    """Мок Redis для проверки логики эндпоинтов Worker Fleet."""

    def __init__(self):
        self.nodes_set: set[str] = set()
        self.keys: dict[str, str] = {}

    async def smembers(self, name: str):
        if name == "worker:nodes":
            return set(self.nodes_set)
        return set()

    async def get(self, key: str):
        return self.keys.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.keys[key] = value

    async def sadd(self, name: str, *values: str):
        if name == "worker:nodes":
            self.nodes_set.update(values)

    async def srem(self, name: str, *values: str):
        if name == "worker:nodes":
            for v in values:
                self.nodes_set.discard(v)

    async def delete(self, *names: str):
        for name in names:
            self.keys.pop(name, None)


@pytest.mark.asyncio
async def test_worker_fleet_and_readiness_endpoints():
    """Тест эндпоинтов GET /api/v2/workers/fleet и /api/v2/workers/readiness."""
    fake_redis = FakeRedisFleet()

    # Создаем сервисный аккаунт для чтения состояния флота (command:read)
    async with AsyncSessionLocal() as db:
        _p, cred, secret = await create_service_credential(
            db,
            subject="service-monitor",
            display_name="Monitoring Service",
            scopes={"command:read"},
        )
        headers = {
            "X-Service-Key-Id": cred.key_id,
            "X-Service-Secret": secret,
        }

    # 1. Заполняем состояние флота: 2 узла (один активный, один устаревший призрак)
    active_card = {
        "node_id": "win_node_primary",
        "hostname": "SRV-WORKER-01",
        "status": "online",
        "capabilities": ["windows", "printers", "winrm", "wmi", "smb_staging"],
        "supported_actions": ["install_printer", "diagnose_host"],
        "last_heartbeat": 1720000000.0,
        "version": "2.0.0",
    }
    await fake_redis.sadd("worker:nodes", "win_node_primary", "ghost_node_stale")
    await fake_redis.set("worker:node:win_node_primary", json.dumps(active_card))

    with patch("app.routers.commands_v2.get_redis_client", return_value=fake_redis):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Чтение списка узлов флота с авторизацией
            fleet_resp = await client.get("/api/v2/workers/fleet", headers=headers)
            assert fleet_resp.status_code == 200, fleet_resp.text
            fleet_data = fleet_resp.json()
            assert fleet_data["total_active"] == 1
            assert len(fleet_data["nodes"]) == 1
            assert fleet_data["nodes"][0]["node_id"] == "win_node_primary"

            # Устаревший ghost_node_stale должен быть автоматически удален из Redis множества
            assert "ghost_node_stale" not in fake_redis.nodes_set

            # Проверка готовности (readiness - открытый k8s probe)
            ready_resp = await client.get("/api/v2/workers/readiness")
            assert ready_resp.status_code == 200
            ready_data = ready_resp.json()
            assert ready_data["ready"] is True
            assert ready_data["active_nodes_count"] == 1
            assert "printers" in ready_data["available_capabilities"]
            assert ready_data["missing_critical_capabilities"] == []

            # 2. Если все узлы исчезают -> readiness False
            await fake_redis.srem("worker:nodes", "win_node_primary")
            await fake_redis.delete("worker:node:win_node_primary")

            unready_resp = await client.get("/api/v2/workers/readiness")
            assert unready_resp.status_code == 200
            unready_data = unready_resp.json()
            assert unready_data["ready"] is False
            assert unready_data["active_nodes_count"] == 0
            assert set(unready_data["missing_critical_capabilities"]) == {
                "windows",
                "printers",
                "winrm",
            }


@pytest.mark.asyncio
async def test_worker_can_execute_action_and_quarantine_integration():
    """Тест логики проверки возможностей воркера и перенаправления в карантин."""
    worker = WindowsExecutionWorker()
    worker.capabilities = ["windows", "winrm", "wmi", "printers", "smb_staging"]

    # 1. Поддерживаемое действие (install_printer)
    can_exec, missing = worker._can_execute_action("install_printer")
    assert can_exec is True
    assert missing == ""

    # 2. Действие, требующее AD, когда AD нет в capabilities
    can_exec, missing = worker._can_execute_action("user_access")
    assert can_exec is False
    assert missing == "ad"

    # 3. Полностью неизвестное действие
    can_exec, missing = worker._can_execute_action("alien_action")
    assert can_exec is False
    assert missing == "handler:alien_action"

    # 4. Интеграция: при получении неподдерживаемой команды в _process_job
    # воркер отправляет её в карантин и возвращает True (для безопасного подтверждения XACK)
    worker.api_client.quarantine_command_v2 = AsyncMock(return_value=True)

    test_msg_id = "1720000000000-5"
    test_stream = "stream:execution_commands:v2"
    job_data = {
        "command_id": str(uuid.uuid4()),
        "action": "unknown_action_without_handler",
        "task_id": "123",
    }

    result = await worker._process_job(test_stream, test_msg_id, job_data)

    assert result is True  # XACK подтвержден, предотвращая зависание в PEL
    worker.api_client.quarantine_command_v2.assert_awaited_once()
    call_kwargs = worker.api_client.quarantine_command_v2.await_args.kwargs
    assert call_kwargs["command_id"] == job_data["command_id"]
    assert call_kwargs["missing_capability"] == "handler:unknown_action_without_handler"
    assert call_kwargs["message_id"] == test_msg_id
