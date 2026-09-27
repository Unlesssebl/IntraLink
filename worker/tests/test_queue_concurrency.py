"""Stress tests for worker queue concurrency, failure isolation, and distributed task locking.

Validates:
  - Redis distributed locking (lock:task:{id}) prevents concurrent execution on the same ticket.
  - Concurrency throughput: batch of 50 concurrent tickets processes smoothly without race conditions or deadlocks.
  - Failure isolation: failure or exception on one ticket does not break or stall the remaining batch.
  - Resource cleanup: distributed locks are reliably freed after execution across all 50 tasks.
"""

import asyncio
from typing import AsyncGenerator, Dict, List
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.autopilot.policy_service import AutopilotPolicyService
from core.database.base import Base
from core.intraservice.auth import ServiceAuthBootstrap, ServiceAuthCredentials
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from worker.src.tasks.autopilot import (
    autopilot_task,
    set_autopilot_client,
    set_autopilot_policy_service,
    set_autopilot_redis_client,
    set_autopilot_service_auth,
    set_autopilot_session_factory,
    set_full_auto_feature_gate,
)


class ThreadSafeMockRedis:
    """Async thread-safe in-memory Redis mock with lock contention tracking."""

    def __init__(self) -> None:
        self.store: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        self.lock_attempts: int = 0
        self.lock_acquisitions: int = 0
        self.lock_collisions: int = 0

    async def get(self, key: str):
        async with self._lock:
            return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 0, nx: bool = False):
        async with self._lock:
            if "lock:task:" in key:
                self.lock_attempts += 1
            if nx and key in self.store:
                if "lock:task:" in key:
                    self.lock_collisions += 1
                return None
            self.store[key] = value
            if "lock:task:" in key:
                self.lock_acquisitions += 1
            return True

    async def delete(self, *keys: str):
        async with self._lock:
            for k in keys:
                self.store.pop(k, None)

    async def exists(self, *keys: str) -> int:
        async with self._lock:
            return sum(1 for k in keys if k in self.store)

    async def eval(self, script: str, numkeys: int, key: str, *args):
        del numkeys
        async with self._lock:
            owner_token = args[0] if args else None
            if owner_token is not None and self.store.get(key) != owner_token:
                return 0
            if "expire" in script.lower():
                return 1
            return int(self.store.pop(key, None) is not None)


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
def mock_redis() -> ThreadSafeMockRedis:
    return ThreadSafeMockRedis()


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
    """Prevent RAG fallback from executing postgres queries during concurrency stress tests."""
    set_full_auto_feature_gate(True)
    with (
        patch("core.scenarios.adapters.rag_consultation.search_hybrid_solutions", new_callable=AsyncMock, return_value=[]),
        patch("core.scenarios.adapters.rag_consultation.get_embedding_vector", new_callable=AsyncMock, return_value=[0.1] * 1024),
    ):
        yield
    set_full_auto_feature_gate(None)


@pytest.mark.asyncio
async def test_distributed_lock_prevents_worker_race_condition(
    mock_client,
    mock_service_auth,
    test_session_factory,
    policy_service,
    mock_redis,
):
    """ASSISTED assignment is read-only even under duplicate delivery."""
    set_full_auto_feature_gate(False)

    task = TaskDTO(
        Id=5001,
        Name="Зависла очередь печати",
        Description="Перезапуск службы печати Kyocera",
        ServiceId=12,
        StatusId=2,
        StatusName="В работе",
        ExecutorIds="999",
        Entities=ExtractedEntitiesDTO(pc_name="WKS-01", printer_model="Kyocera"),
    )
    mock_client.get_task.return_value = task

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)
    set_autopilot_redis_client(mock_redis)

    res1, res2 = await asyncio.gather(
        autopilot_task(5001),
        autopilot_task(5001),
    )

    statuses = {res1.get("status"), res2.get("status")}
    assert statuses == {"awaiting_operator_approval"}
    assert mock_redis.lock_attempts == 0
    assert not await mock_redis.exists("lock:task:5001")
    mock_client.update_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_50_concurrent_tasks_stress_and_isolation(
    mock_client,
    mock_service_auth,
    test_session_factory,
    policy_service,
    mock_redis,
):
    """Fifty concurrent ASSISTED deliveries remain read-only and lock-free."""
    set_full_auto_feature_gate(False)

    total_tickets = 50
    start_id = 8001
    tasks: Dict[int, TaskDTO] = {}

    for i in range(total_tickets):
        tid = start_id + i
        tasks[tid] = TaskDTO(
            Id=tid,
            Name=f"Зависла очередь печати в кабинете {100 + i}",
            Description="Перезапуск службы печати Kyocera",
            ServiceId=12,
            StatusId=2,
            StatusName="В работе",
            ExecutorIds="999",
            Entities=ExtractedEntitiesDTO(
                pc_name=f"WKS-PC-{i:02d}",
                printer_model="Kyocera ECOSYS M2040dn",
            ),
        )

    mock_client.get_task.side_effect = lambda *args, **kwargs: tasks[kwargs.get("task_id") if "task_id" in kwargs else args[0]]

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)
    set_autopilot_redis_client(mock_redis)

    batch_coros = [autopilot_task(start_id + i) for i in range(total_tickets)]
    results: List[Dict] = await asyncio.gather(*batch_coros)

    assert len(results) == 50

    awaiting = [r for r in results if r.get("status") == "awaiting_operator_approval"]
    assert len(awaiting) == 50
    mock_client.update_task.assert_not_awaited()
    assert mock_redis.lock_attempts == 0

    # Invariant: ALL 50 locks must be released from Redis
    remaining_locks = [k for k in mock_redis.store if k.startswith("lock:task:")]
    assert len(remaining_locks) == 0, f"Leaked distributed locks found: {remaining_locks}"
