"""Universal Scenario Lifecycle Core Matrix tests.

Validates the 7 fundamental reliability invariants defined in:
  - docs/architecture/v2-universal-scenario-core.md
  - docs/plans/v2-universal-scenario-core-plan.md

Invariants covered:
  1. Mixed Batch (Heterogeneous batch): supported tickets resolve to Status 3,
     unsupported tickets escalate to Status 2, failure on one ticket is isolated.
  2. OCC Version Guard (Stale Approval): status or last_event_id mismatch returns 409 Conflict.
  3. Pre-Execution Optimistic Lock: human assignment preemption safely aborts worker execution.
  4. Cooperative Interruption (Reclaim): Redis abort flag immediately halts execution with ExecutionAbortedException.
  5. Fast Socket Probe: unreachable host probes strictly within <= 1.5s lifecycle timeout.
  6. Attachment Heuristic (Scan Only): missing facts with attachments escalates to human without silly clarification questions.
  7. Zero-Plaintext Policy Audit: passwords never leak to public comments, logs, or string representations.
"""

import asyncio
import time
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.src.features.autopilot.schemas import ApprovePlanRequest
from api.src.features.autopilot.service import AutopilotService
from core.ad.password import SecretPassword, generate_secure_password, mask_password
from core.ad.provisioning import AccountProvisioningReceipt, AccountProvisioningService
from core.autopilot.dto import AutopilotPolicyDTO, AutopilotPolicyUpdateDTO
from core.autopilot.policy_service import AutopilotPolicyService
from core.database.base import Base
from core.diagnostic.ports import FastSocketProbe
from core.intraservice.auth import ServiceAuthBootstrap, ServiceAuthCredentials
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO, TaskLifetimeEventDTO
from core.scenarios.adapters.account_create import AccountCreateScenario
from core.scenarios.adapters.printer_spooler_restart import PrinterSpoolerRestartScenario
from core.scenarios.base import ExecutionAbortedException, ScenarioExecutionResult
from core.scenarios.orchestrator import ScenarioLifecycleOrchestrator
from core.scenarios.registry import ScenarioRegistry
from worker.src.tasks.autopilot import (
    autopilot_task,
    set_autopilot_client,
    set_autopilot_policy_service,
    set_autopilot_redis_client,
    set_autopilot_registry,
    set_autopilot_service_auth,
    set_autopilot_session_factory,
)


class MockRedis:
    """In-memory Redis mock for distributed locking, caching and abort flags."""

    def __init__(self) -> None:
        self.store = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 0, nx: bool = False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, *keys: str):
        for k in keys:
            self.store.pop(k, None)

    async def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self.store)


@pytest.fixture
async def test_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest.fixture
def mock_redis() -> MockRedis:
    return MockRedis()


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock(spec=IntraServiceClient)
    client.update_task.return_value = True
    client.get_task_lifetime.return_value = []
    return client


@pytest.fixture
def mock_service_auth() -> AsyncMock:
    auth_service = AsyncMock(spec=ServiceAuthBootstrap)
    auth_service.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="Ym90OnBhc3M=",
        bot_user_id=999,
        login="alen_assistant",
    )
    return auth_service


@pytest.fixture
def policy_service(test_session_factory, mock_redis) -> AutopilotPolicyService:
    return AutopilotPolicyService(session_factory=test_session_factory, redis_client=mock_redis)


@pytest.fixture(autouse=True)
def mock_rag_search():
    """Prevent RAG fallback from executing postgres queries during core matrix unit testing."""
    with (
        patch("core.scenarios.adapters.rag_consultation.search_hybrid_solutions", new_callable=AsyncMock, return_value=[]),
        patch("core.scenarios.adapters.rag_consultation.get_embedding_vector", new_callable=AsyncMock, return_value=[0.1] * 1024),
    ):
        yield


# ==============================================================================
# Invariant 1: Mixed Batch (Heterogeneous Batch Execution & Failure Isolation)
# ==============================================================================

@pytest.mark.asyncio
async def test_mixed_batch_execution(
    mock_client,
    mock_service_auth,
    test_session_factory,
    policy_service,
    mock_redis,
):
    """Simulate a batch of 3 heterogeneous tickets:

    1. Supported spooler restart -> resolves to Status 3.
    2. Supported user onboarding -> resolves to Status 3.
    3. Unsupported ticket (e.g. office furniture) -> escalates to human (Status 2).
    Failure on one ticket is completely isolated from the batch.
    """
    await policy_service.update_policy(
        "printer_spooler_restart",
        AutopilotPolicyUpdateDTO(mode="FULL_AUTO", min_confidence=0.5),
    )
    await policy_service.update_policy(
        "account_create",
        AutopilotPolicyUpdateDTO(mode="FULL_AUTO", min_confidence=0.5),
    )

    t1 = TaskDTO(
        Id=1001,
        Name="Зависла очередь печати",
        Description="Перезапуск службы печати Kyocera",
        ServiceId=12,
        ServiceName="Оргтехника и печать",
        StatusId=2,
        StatusName="В работе",
        ExecutorIds="999",
        Entities=ExtractedEntitiesDTO(pc_name="WKS-PRN-01", printer_model="Kyocera ECOSYS"),
    )
    t2 = TaskDTO(
        Id=1002,
        Name="Заявка на пользователя Directum",
        Description="Создать учетную запись новому сотруднику",
        ServiceId=55,
        ServiceName="Заявка на пользователя DIRECTUM",
        StatusId=2,
        StatusName="В работе",
        ExecutorIds="999",
        Entities=ExtractedEntitiesDTO(
            first_name="Алексей",
            last_name="Смирнов",
            department="Бухгалтерия",
            title="Экономист",
        ),
    )
    t3 = TaskDTO(
        Id=1003,
        Name="Собрать офисный стол и переставить шкаф",
        Description="В кабинете 402 требуется сборка мебели",
        ServiceId=999,
        ServiceName="Хозяйственные работы",
        StatusId=2,
        StatusName="В работе",
        ExecutorIds="999",
        Entities=ExtractedEntitiesDTO(),
    )

    tasks_map = {1001: t1, 1002: t2, 1003: t3}
    mock_client.get_task.side_effect = lambda *args, **kwargs: tasks_map[kwargs.get("task_id") if "task_id" in kwargs else args[0]]

    custom_reg = ScenarioRegistry()
    custom_reg.register(PrinterSpoolerRestartScenario())
    custom_reg.register(AccountCreateScenario())
    set_autopilot_registry(custom_reg)
    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)
    set_autopilot_redis_client(mock_redis)

    try:
        with (
            patch("core.scenarios.adapters.printer_spooler_restart.FastSocketProbe.probe", new_callable=AsyncMock) as mock_probe,
            patch("core.scenarios.adapters.printer_spooler_restart.WinRMExecutor.run_powershell", new_callable=AsyncMock) as mock_winrm,
            patch.object(AccountProvisioningService, "provision", new_callable=AsyncMock) as mock_ad_create,
        ):
            mock_probe.return_value = AsyncMock(is_online=True, ports={5985: True})
            mock_winrm.return_value = (0, "CLEARED:2;STATUS:Running", "")
            mock_ad_create.return_value = AccountProvisioningReceipt(
                sam_account_name="smirnov.a",
                upn="smirnov.a@corp.loc",
                user_dn="CN=Смирнов Алексей,OU=Accounting,DC=corp,DC=loc",
                full_name="Смирнов Алексей",
                credentials_written=True,
            )

            # Process mixed batch concurrently
            results = await asyncio.gather(
                autopilot_task(1001),
                autopilot_task(1002),
                autopilot_task(1003),
            )
    finally:
        set_autopilot_registry(None)

    res1, res2, res3 = results

    # Ticket 1 (Spooler) resolved
    assert res1["status"] == "resolved"
    assert res1["scenario"] == "printer_spooler_restart"
    assert res1["target_status_id"] == 3

    # Ticket 2 (Onboarding) resolved
    assert res2["status"] == "resolved"
    assert res2["scenario"] == "account_create"
    assert res2["target_status_id"] == 3

    # Ticket 3 (Unsupported) cleanly escalated to human without breaking the batch
    assert res3["status"] == "unmatched"

    # Verify IntraService updates
    updates = [call.kwargs for call in mock_client.update_task.call_args_list]
    statuses_updated = {u["task_id"]: u.get("status_id") for u in updates if "status_id" in u}

    assert statuses_updated[1001] == 3
    assert statuses_updated[1002] == 3
    assert statuses_updated[1003] == 2  # Escalated to human in Status 2


# ==============================================================================
# Invariant 2: OCC Version Guard (Stale Approval Defense)
# ==============================================================================

@pytest.mark.asyncio
async def test_occ_version_guard_stale_approval(
    mock_client,
    test_session_factory,
    policy_service,
    mock_redis,
):
    """Verify OCC Version Guard raises HTTP 409 Conflict when status or lifetime events change."""
    service = AutopilotService(client=mock_client, policy_service=policy_service)

    # 1. Status changed while supervisor was reviewing (Status 1 -> 3)
    current_task = TaskDTO(
        Id=2001,
        Name="Сбросить кэш Directum",
        StatusId=3,  # Already resolved
        StatusName="Выполнена",
        Entities=ExtractedEntitiesDTO(pc_name="WKS-01"),
    )
    mock_client.get_task.return_value = current_task

    stale_status_req = ApprovePlanRequest(expected_status_id=1, last_event_id=10)

    async with test_session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await service.approve_plan(
                ticket_id=2001,
                req=stale_status_req,
                operator_username="ivanov",
                session=session,
                redis_client=mock_redis,
            )
        assert exc_info.value.status_code == 409
        assert "Статус заявки изменился с 1 на 3" in exc_info.value.detail

    # 2. Lifetime changed while supervisor was reviewing (new event added)
    task_same_status = TaskDTO(
        Id=2002,
        Name="Сбросить кэш Directum",
        StatusId=2,
        StatusName="В работе",
        Entities=ExtractedEntitiesDTO(pc_name="WKS-01"),
    )
    mock_client.get_task.return_value = task_same_status
    mock_client.get_task_lifetime.return_value = [
        TaskLifetimeEventDTO(Id=105, Date="2026-09-25", Editor="applicant", Comment="Я уже сам перезагрузил")
    ]

    stale_event_req = ApprovePlanRequest(expected_status_id=2, last_event_id=100)

    async with test_session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await service.approve_plan(
                ticket_id=2002,
                req=stale_event_req,
                operator_username="ivanov",
                session=session,
                redis_client=mock_redis,
            )
        assert exc_info.value.status_code == 409
        assert "История тикета изменилась во время рассмотрения" in exc_info.value.detail


# ==============================================================================
# Invariant 3: Pre-Execution Optimistic Lock (Human Preemption)
# ==============================================================================

@pytest.mark.asyncio
async def test_pre_execution_optimistic_lock_reassigned_to_human(
    mock_client,
    mock_service_auth,
    test_session_factory,
    policy_service,
    mock_redis,
):
    """When a ticket is reassigned to human engineer(s) in IntraService, worker immediately skips execution."""
    reassigned_task = TaskDTO(
        Id=3001,
        Name="Не печатает принтер",
        ServiceId=12,
        StatusId=2,
        StatusName="В работе",
        ExecutorIds="555",  # Reassigned to engineer 555 (bot is 999)
        Entities=ExtractedEntitiesDTO(pc_name="WKS-01"),
    )
    mock_client.get_task.return_value = reassigned_task

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)
    set_autopilot_redis_client(mock_redis)

    res = await autopilot_task(3001)

    assert res["status"] == "skipped"
    assert res["reason"] == "assigned_to_human"
    # Worker must not make any mutations or update status
    mock_client.update_task.assert_not_called()


# ==============================================================================
# Invariant 4: Cooperative Interruption (Human Reclaim)
# ==============================================================================

@pytest.mark.asyncio
async def test_cooperative_interruption_reclaim(
    mock_client,
    mock_service_auth,
    test_session_factory,
    policy_service,
    mock_redis,
):
    """When autopilot:abort:{id} flag is active in Redis, execution is halted immediately."""
    task = TaskDTO(
        Id=4001,
        Name="Не печатает принтер",
        ServiceId=12,
        StatusId=2,
        StatusName="В работе",
        ExecutorIds="999",
        Entities=ExtractedEntitiesDTO(pc_name="WKS-01"),
    )
    mock_client.get_task.return_value = task

    # Set abort flag in Redis (simulating operator clicking 'Reclaim')
    await mock_redis.set("autopilot:abort:4001", "operator_petrov")

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)
    set_autopilot_redis_client(mock_redis)

    # 1. Verify autopilot_task aborts
    res = await autopilot_task(4001)
    assert res["status"] == "aborted"
    assert res["reason"] == "reclaimed_by_operator"
    mock_client.update_task.assert_not_called()

    # 2. Verify ScenarioLifecycleOrchestrator raises ExecutionAbortedException
    orchestrator = ScenarioLifecycleOrchestrator(
        client=mock_client,
        redis_conn=mock_redis,
        policy_service=policy_service,
    )
    dummy_scenario = AsyncMock()
    dummy_scenario.scenario_key = "dummy"

    with pytest.raises(ExecutionAbortedException) as exc_info:
        await orchestrator.execute_and_audit(
            scenario=dummy_scenario,
            task=task,
            policy=AutopilotPolicyDTO(scenario_key="dummy", mode="FULL_AUTO"),
            auth_b64="auth",
            initiator="autopilot",
        )
    assert "Execution aborted for ticket #4001: reclaimed by operator" in str(exc_info.value)
    dummy_scenario.execute.assert_not_called()


@pytest.mark.asyncio
async def test_assisted_failure_keeps_ticket_in_progress_and_writes_hidden_report(
    mock_client, policy_service, mock_redis
):
    task = TaskDTO(Id=4002, StatusId=2, StatusName="В работе")
    scenario = AsyncMock()
    scenario.scenario_key = "install_printer"
    scenario.name = "Установка принтера"
    scenario.execute.return_value = ScenarioExecutionResult(
        success=False,
        action_taken="install_printer",
        resolution_comment="",
        technical_note="Windows executor unavailable; preflight completed.",
        target_status_id=2,
        error="printer_executor_unavailable",
        metadata={"failure_code": "printer_executor_unavailable"},
    )
    orchestrator = ScenarioLifecycleOrchestrator(mock_client, mock_redis, policy_service)

    result = await orchestrator.execute_and_audit(
        scenario=scenario,
        task=task,
        policy=AutopilotPolicyDTO(scenario_key="install_printer", mode="ASSISTED"),
        auth_b64="auth",
        initiator="supervisor:ivanov",
        update_circuit_breaker=False,
    )

    assert result.success is False
    update = mock_client.update_task.await_args.kwargs
    assert update["status_id"] == 2
    assert update["is_private"] is True
    assert "printer_executor_unavailable" in update["comment"]
    assert "preflight completed" in update["comment"]


# ==============================================================================
# Invariant 5: Fast Socket Probe (Socket Timeout Boundary <= 1.5s)
# ==============================================================================

@pytest.mark.asyncio
async def test_fast_socket_probe_timeout_boundary():
    """Verify that FastSocketProbe completes probe strictly within <= 1.55s,

    preventing worker thread pool starvation from OS TCP connection timeouts (21s).
    """
    probe = FastSocketProbe(default_timeout_sec=1.5)

    async def hanging_connect(*args, **kwargs):
        # Emulate 21s OS TCP timeout by hanging
        await asyncio.sleep(25.0)
        raise TimeoutError()

    with patch("asyncio.open_connection", side_effect=hanging_connect):
        start = time.monotonic()
        result = await probe.probe(host="offline-workstation.corp.loc", ports=[5985, 9100])
        elapsed = time.monotonic() - start

    assert elapsed <= 1.55, f"Probe took {elapsed:.2f}s, exceeding 1.5s lifecycle limit"
    assert result.is_online is False
    assert result.ports.get(5985) is False
    assert result.ports.get(9100) is False


# ==============================================================================
# Invariant 6: Attachment Heuristic (Scan Only -> Escalate to Human)
# ==============================================================================

@pytest.mark.asyncio
async def test_attachment_heuristic_scan_only_escalates_to_human(
    mock_client,
    mock_service_auth,
    test_session_factory,
    policy_service,
    mock_redis,
):
    """If facts are missing but attachments are present, escalate to engineer instead of asking silly questions."""
    await policy_service.update_policy(
        "printer_spooler_restart",
        AutopilotPolicyUpdateDTO(mode="FULL_AUTO", min_confidence=0.5),
    )

    task_with_scan = TaskDTO(
        Id=6001,
        Name="Не печатает принтер",
        Description="Зависла очередь печати, фото стикера во вложении",
        ServiceId=12,
        ServiceName="Оргтехника и печать",
        StatusId=1,
        StatusName="Новая",
        ExecutorIds="999",
        Entities=ExtractedEntitiesDTO(pc_name=""),  # Missing fact
        Attachments=[{"Id": 801, "Name": "printer_sticker_photo.jpg", "Size": 102400}],
    )
    mock_client.get_task.return_value = task_with_scan

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)
    set_autopilot_redis_client(mock_redis)

    res = await autopilot_task(6001)

    assert res["status"] == "escalated_attachments_present"
    assert res["attachments_count"] == 1

    # Verified: moved to Status 2 (Human Engineer), NOT Status 6 (Clarification Loop)
    updates = [call.kwargs for call in mock_client.update_task.call_args_list]
    assert len(updates) == 1
    assert updates[0]["status_id"] == 2
    assert "Требуется визуальный осмотр вложений" in updates[0]["comment"]
    assert updates[0]["is_private"] is True


# ==============================================================================
# Invariant 7: Zero-Plaintext Policy Audit
# ==============================================================================

@pytest.mark.asyncio
async def test_zero_plaintext_policy_audit(mock_client, policy_service):
    """Verify that credentials conform to Zero-Plaintext Policy:

    1. SecretPassword hides plaintext in repr() and str().
    2. Password generation adheres to AD complexity without ambiguous chars (0, O, 1, l, I).
    3. Public resolution comments NEVER contain raw plaintext passwords.
    4. Passwords are masked as ***REDACTED*** in logs and sanitized notes.
    """
    pwd = generate_secure_password(length=14)

    # 1. Wrapper protection
    assert isinstance(pwd, SecretPassword)
    assert str(pwd) == "***REDACTED***"
    assert repr(pwd) == "SecretPassword('***REDACTED***')"

    # 2. AD Complexity & unambiguous character validation
    raw_val = pwd.get_secret_value()
    assert len(raw_val) >= 12
    for forbidden in ("0", "O", "o", "1", "l", "I", "i"):
        assert forbidden not in raw_val

    # 3. Mask function
    assert mask_password(raw_val) == "***REDACTED***"

    # 4. Scenario execution check: public comment must never leak password
    scenario = AccountCreateScenario()
    task = TaskDTO(
        Id=7001,
        Name="Создать учетную запись Directum",
        ServiceId=55,
        Entities=ExtractedEntitiesDTO(
            first_name="Елена",
            last_name="Кузнецова",
            department="Кадры",
            title="Специалист",
        ),
    )

    with patch.object(scenario.provisioner, "provision", new_callable=AsyncMock) as mock_ad_sync:
        mock_ad_sync.return_value = AccountProvisioningReceipt(
            sam_account_name="kuznetsova.e",
            upn="kuznetsova.e@corp.loc",
            user_dn="CN=Кузнецова Елена,OU=HR,DC=corp,DC=loc",
            full_name="Кузнецова Елена",
            credentials_written=True,
        )
        res: ScenarioExecutionResult = await scenario.execute(
            task,
            AutopilotPolicyDTO(scenario_key="account_create", mode="FULL_AUTO"),
        )

    assert res.success is True
    # The raw password must NOT appear anywhere in the public resolution comment
    assert raw_val not in res.resolution_comment
    assert "kuznetsova.e" in res.resolution_comment
    assert "pwdLastSet=0" in res.resolution_comment
    assert "защищённые поля заявки" in res.resolution_comment
    assert "Временный пароль:" not in res.technical_note
    assert "password" not in res.model_dump_json().lower()
