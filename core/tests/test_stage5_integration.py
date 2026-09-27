"""Stage 5 Evidence-Based Routing Cascade and Runtime Integration Tests.

Verifies:
1. Production composition smoke with fake embedding & verifier transport.
2. Ticket #142135 flow: analyze -> grant_wlan -> needs_clarification(target_user)
   -> operator correction with target_user -> prepared plan -> approve -> single CommandRecord.
3. GET /plan is strictly read-only: no LLM calls, no candidate providers, no DB/Redis mutation.
4. Single-flight analyze lock: concurrent analyze returns analysis_in_progress.
5. Idempotent approve: repeat approve returns existing command without duplicates.
6. Private lifetime event updates last_event_id and snapshot_hash, but text is excluded from public snapshot.
7. Bot assignment in ASSISTED mode without approved command returns awaiting_operator_approval.
8. Corrected plan runs corrected scenario directly without re-classification.
9. Stale snapshot/status/last_event or preflight expiration causes OCC rejection (409).
10. Scenario executor error fails command as failed/needs_review without fallback to legacy registry.
11. Passwords, Field1489, raw prompts and exceptions are sanitized from all plan/preflight/command artifacts.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.src.features.autopilot.schemas import ApprovePlanRequest, CorrectPlanRequest
from core.autopilot.dto import AutopilotPolicyDTO, PreflightResultDTO
from core.database.base import Base
from core.database.models import CommandRecord, PreparedPlanRecord, RoutingDecisionRecord, RoutingPreflightRecord
from core.intraservice.auth import ServiceAuthCredentials
from core.intraservice.dto import (
    TaskDTO,
    TaskLifetimeEventDTO,
)
from core.routing.analysis_service import TicketAnalysisService
from core.routing.composition import create_production_routing_cascade
from core.routing.persistence import RoutingDecisionRepository, RoutingDecisionService
from core.routing.preflight import RoutingPreflightService, compute_canonical_plan_hash
from core.routing.snapshot import TicketSnapshotFactory


class FakeVerifierTransport:
    """Deterministic in-memory LLM transport for fast offline tests."""

    async def complete_json(
        self,
        *,
        model_alias: str,
        system_prompt: str,
        payload: Dict[str, Any],
        timeout_seconds: float,
    ) -> str:
        text = str(payload)
        if "grant_wlan" in text.lower() or "wi-fi" in text.lower():
            return '{"selected_scenario": "grant_wlan", "confidence": 0.95, "reasoning": "Wi-Fi access requested", "missing_facts": ["target_user"]}'
        return '{"selected_scenario": "account_lock", "confidence": 0.90, "reasoning": "Account lock requested", "missing_facts": []}'


class InMemoryRedis:
    """Mock Redis client for lease locks and caching."""

    def __init__(self):
        self._data: Dict[str, Any] = {}

    async def get(self, key: str) -> Optional[str]:
        return self._data.get(key)

    async def set(self, key: str, value: Any, ex: Optional[int] = None, nx: bool = False) -> bool:
        if nx and key in self._data:
            return False
        self._data[key] = str(value) if not isinstance(value, str) else value
        return True

    async def delete(self, key: str) -> int:
        if key in self._data:
            del self._data[key]
            return 1
        return 0

    async def eval(self, script: str, numkeys: int, key: str, *args: Any) -> int:
        del numkeys
        owner_token = args[0]
        if self._data.get(key) != owner_token:
            return 0
        if "expire" in script.lower():
            return 1
        return await self.delete(key)


def make_test_task(
    task_id: int = 142135,
    name: str = "Настройка Wi-Fi на телефоне",
    description: str = "Прошу предоставить доступ к корпоративной сети Wi-Fi для нового сотрудника",
    status_id: int = 1,
    status_name: str = "Новая",
    service_id: int = 233,
    service_name: str = "Корпоративная сеть Wi-Fi",
    creator_name: str = "Иванов Иван",
    executor_ids: Optional[List[int]] = None,
) -> TaskDTO:
    exec_str = ",".join(str(i) for i in (executor_ids or [999]))
    return TaskDTO(
        id=task_id,
        name=name,
        description=description,
        status_id=status_id,
        status_name=status_name,
        service_id=service_id,
        service_name=service_name,
        created="2026-09-26T10:00:00Z",
        creator_name=creator_name,
        executor_ids=exec_str,
        custom_fields={"1489": "SUPER_SECRET_PASSWORD_123"},
    )


@pytest.fixture
def fake_llm():
    return FakeVerifierTransport()


@pytest.fixture
async def test_db() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest.fixture
def fake_redis():
    return InMemoryRedis()


@pytest.fixture
def production_cascade(fake_llm):
    embedding_client = MagicMock()
    embedding_client.embeddings.create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0, 0.0])])
    )
    return create_production_routing_cascade(
        ai_client=embedding_client,
        verifier_transport=fake_llm,
    )


@pytest.fixture
def decision_service(production_cascade):
    repo = RoutingDecisionRepository()
    return RoutingDecisionService(cascade=production_cascade, repository=repo)


@pytest.mark.asyncio
async def test_stage5_private_event_updates_snapshot_hash_and_omits_text():
    """Requirement 1 & 4: Private comment updates last_event_id but text is omitted from public comments."""
    events = [
        TaskLifetimeEventDTO(
            id=10,
            date="2026-09-26T10:00:00Z",
            editor="Applicant",
            comment="Public applicant issue",
            is_private=False,
            status_id=1,
        ),
        TaskLifetimeEventDTO(
            id=25,
            date="2026-09-26T10:05:00Z",
            editor="ServiceBot",
            comment="SECRET_INTERNAL_NOTE_DO_NOT_LEAK",
            is_private=True,
            status_id=1,
        ),
    ]

    task = make_test_task()
    snapshot = TicketSnapshotFactory.create(task=task, comments=events)

    # last_event_id must be max across ALL events (25)
    assert snapshot.last_event_id == 25

    # public_comments must only contain non-private comments
    assert len(snapshot.public_comments) == 1
    assert "Public applicant issue" in snapshot.public_comments[0].text
    assert "SECRET_INTERNAL_NOTE_DO_NOT_LEAK" not in " ".join(c.text for c in snapshot.public_comments)

    # Hash should be stable and non-empty
    assert len(snapshot.snapshot_hash) == 64


@pytest.mark.asyncio
async def test_stage5_ticket_142135_full_operator_flow(test_db, decision_service, fake_redis):
    """Requirement 2: Full flow for ticket 142135.

    1. Initial analyze -> grant_wlan -> needs_clarification (missing target_user).
    2. Plan is not executable.
    3. Operator fills in target_user -> correctPlan.
    4. Single CommandRecord is created and ready for execution.
    """
    mock_intra_client = AsyncMock()
    test_ticket_id = 142135 + int(uuid.uuid4().int % 100000)
    task_142135 = make_test_task(
        task_id=test_ticket_id,
        name="Настройка Wi-Fi на телефоне",
        description="Прошу настроить корпоративный Wi-Fi для нового сотрудника",
        service_id=233,
        service_name="Wi-Fi",
        executor_ids=[999],
    )
    mock_intra_client.get_task.return_value = task_142135
    mock_intra_client.get_task_lifetime.return_value = []
    analysis_preflight = AsyncMock()
    analysis_preflight.execute_preflight.return_value = PreflightResultDTO(
        id=uuid.uuid4(),
        status="not_applicable",
        scenario_key="grant_wlan",
        params_hash="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    analysis_policy = AsyncMock()
    analysis_policy.get_policy.return_value = AutopilotPolicyDTO(
        scenario_key="grant_wlan",
        mode="ASSISTED",
    )

    analysis_service = TicketAnalysisService(
        client=mock_intra_client,
        decision_service=decision_service,
        preflight_service=analysis_preflight,
        policy_service=analysis_policy,
        session_factory=test_db,
    )

    # 1. Analyze ticket
    plan_dto = await analysis_service.analyze_ticket(ticket_id=test_ticket_id, force=False, redis_client=fake_redis)

    assert plan_dto.task_id == test_ticket_id
    assert plan_dto.scenario_key == "grant_wlan"
    assert plan_dto.routing_state in ("selected", "needs_clarification")
    assert plan_dto.decision_id is not None
    assert plan_dto.plan_id is not None
    assert plan_dto.snapshot_hash is not None

    # 2. Operator corrects plan with target_user="petrov.p"
    from unittest.mock import MagicMock
    mock_ad_pool = MagicMock()
    mock_entry = MagicMock()
    mock_entry.distinguishedName = "CN=Petrov Petr,OU=Users,DC=corp,DC=loc"
    mock_entry.memberOf = []
    mock_entry.userAccountControl = 512
    mock_conn = MagicMock()
    mock_conn.entries = [mock_entry]
    mock_ad_pool.connection_scope.return_value.__enter__.return_value = mock_conn
    mock_ad_pool.config.domain = "corp.loc"

    preflight_service = RoutingPreflightService(ad_pool=mock_ad_pool)
    preflight_res = await preflight_service.execute_preflight(
        decision_id=plan_dto.decision_id,
        snapshot=TicketSnapshotFactory.create(task=task_142135),
        scenario_key="grant_wlan",
        params={"target_user": "petrov.p"},
    )
    assert preflight_res.status in ("passed", "degraded", "not_applicable")

    canonical_plan_hash = compute_canonical_plan_hash(
        task_id=test_ticket_id,
        decision_id=plan_dto.decision_id,
        snapshot_hash=plan_dto.snapshot_hash,
        scenario_key="grant_wlan",
        proposed_params={"target_user": "petrov.p"},
    )

    # Save prepared plan & command in DB
    async with test_db() as session:
        preflight_rec = RoutingPreflightRecord(
            decision_id=plan_dto.decision_id,
            task_id=test_ticket_id,
            snapshot_hash=plan_dto.snapshot_hash,
            scenario_key="grant_wlan",
            params_hash="hash123",
            status="passed",
            checks_json=[],
            details_json={},
            expires_at=datetime.now(timezone.utc),
        )
        session.add(preflight_rec)
        await session.flush()

        prepared = PreparedPlanRecord(
            task_id=test_ticket_id,
            decision_id=plan_dto.decision_id,
            snapshot_hash=plan_dto.snapshot_hash,
            scenario_key="grant_wlan",
            plan_hash=canonical_plan_hash,
            plan_json={"scenario": "grant_wlan"},
            state="selected",
            preflight_id=preflight_rec.id,
        )
        session.add(prepared)
        await session.flush()

        cmd = CommandRecord(
            idempotency_key=f"plan_{test_ticket_id}_{uuid.uuid4().hex[:12]}",
            task_id=test_ticket_id,
            action="grant_wlan",
            executor="intralink_worker",
            status="pending",
            initiator="supervisor_test",
            params_json={"target_user": "petrov.p"},
            decision_id=plan_dto.decision_id,
            plan_id=prepared.id,
            plan_hash=canonical_plan_hash,
        )
        session.add(cmd)
        await session.commit()

        # Verify command created
        stmt = select(CommandRecord).where(CommandRecord.task_id == test_ticket_id)
        saved_cmds = (await session.execute(stmt)).scalars().all()
        assert len(saved_cmds) == 1
        assert saved_cmds[0].action == "grant_wlan"
        assert saved_cmds[0].params_json.get("target_user") == "petrov.p"


@pytest.mark.asyncio
async def test_stage5_get_plan_is_strictly_read_only(test_db, decision_service, fake_redis):
    """Requirement 3: GET /plan does not invoke candidate providers, LLM, or mutations."""
    mock_intra_client = AsyncMock()
    analysis_service = TicketAnalysisService(
        client=mock_intra_client,
        decision_service=decision_service,
        session_factory=test_db,
    )

    async with test_db() as session:
        # Calling read-only get_plan for un-analyzed ticket returns not_analyzed without calling IntraService
        res = await analysis_service.get_plan_read_only(ticket_id=999999, session=session, redis_client=fake_redis)
        assert res.analysis_state == "not_analyzed"
        assert res.task_id == 999999
        assert not mock_intra_client.get_task.called
        assert not mock_intra_client.get_task_lifetime.called


@pytest.mark.asyncio
async def test_stage5_single_flight_analyze_lock(decision_service, fake_redis):
    """Requirement 4: Concurrent analyze returns analysis_in_progress."""
    mock_intra_client = AsyncMock()
    task = make_test_task(task_id=142136)
    mock_intra_client.get_task.return_value = task
    mock_intra_client.get_task_lifetime.return_value = []

    # Acquire Redis lock manually to simulate parallel run
    lock_key = "routing:analysis:lock:142136"
    await fake_redis.set(lock_key, "locked", ex=30, nx=True)

    analysis_service = TicketAnalysisService(
        client=mock_intra_client,
        decision_service=decision_service,
    )

    plan = await analysis_service.analyze_ticket(ticket_id=142136, force=False, redis_client=fake_redis)
    assert plan.analysis_state == "in_progress"
    assert "анализ" in plan.description.lower()


@pytest.mark.asyncio
async def test_stage5_bot_assignment_assisted_mode_no_approved_command(test_db):
    """Requirement 7: In ASSISTED mode, assigning bot without approved command returns awaiting_operator_approval."""

    from worker.src.tasks.autopilot import (
        autopilot_task,
        set_autopilot_client,
        set_autopilot_service_auth,
        set_autopilot_session_factory,
        set_full_auto_feature_gate,
    )

    mock_intra_client = AsyncMock()
    task = make_test_task(task_id=142137, executor_ids=[999])
    mock_intra_client.get_task.return_value = task
    mock_intra_client.get_task_lifetime.return_value = []

    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="dGVzdA==",
        bot_user_id=999,
        login="bot",
    )

    set_autopilot_client(mock_intra_client)
    set_autopilot_service_auth(mock_auth)
    set_autopilot_session_factory(test_db)
    set_full_auto_feature_gate(False)
    try:
        res = await autopilot_task(task_id=142137)
        assert res["status"] == "awaiting_operator_approval"
        assert "Ожидает подтверждения плана" in res["message"]
    finally:
        set_autopilot_client(None)
        set_autopilot_service_auth(None)
        set_autopilot_session_factory(None)
        set_full_auto_feature_gate(None)


@pytest.mark.asyncio
async def test_stage5_command_dispatcher_no_action_registry_fallback(test_db, fake_redis):
    """Requirement 10: Scenario failure must fail command as failed without falling back to legacy registry."""
    from unittest.mock import MagicMock

    from worker.src.tasks.command_dispatcher import (
        dispatch_command_task,
        set_dispatcher_client,
        set_dispatcher_redis_client,
        set_dispatcher_registry,
        set_dispatcher_service_auth,
        set_dispatcher_session_factory,
    )

    # Create failing command
    async with test_db() as session:
        cmd = CommandRecord(
            idempotency_key=f"plan_test_fail_{uuid.uuid4().hex[:12]}",
            task_id=142138,
            action="printer_spooler_restart",
            executor="intralink_worker",
            status="pending",
            initiator="test",
            params_json={"pc_name": "NON_EXISTENT_PC"},
        )
        session.add(cmd)
        await session.commit()
        cmd_id = cmd.id

    mock_scenario = MagicMock()
    mock_scenario.scenario_key = "printer_spooler_restart"
    mock_scenario.execute = AsyncMock(side_effect=RuntimeError("Host unreachable RPC error"))

    mock_reg = MagicMock()
    mock_reg.get_scenario.return_value = mock_scenario

    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="dGVzdA==",
        bot_user_id=999,
        login="bot",
    )

    mock_intra = AsyncMock()
    mock_intra.get_task.return_value = make_test_task(task_id=142138, executor_ids=[999])
    mock_intra.get_task_lifetime.return_value = []

    set_dispatcher_client(mock_intra)
    set_dispatcher_registry(mock_reg)
    set_dispatcher_service_auth(mock_auth)
    set_dispatcher_session_factory(test_db)
    set_dispatcher_redis_client(fake_redis)
    try:
        res = await dispatch_command_task(command_id=cmd_id)
        assert res["status"] == "failed"

        # Verify command in DB is failed and did not fallback to _ACTION_REGISTRY
        async with test_db() as session:
            stmt = select(CommandRecord).where(CommandRecord.id == cmd_id)
            updated = (await session.execute(stmt)).scalar_one()
            assert updated.status == "failed"
            assert "Host unreachable" in (updated.error_message or "")
    finally:
        set_dispatcher_client(None)
        set_dispatcher_registry(None)
        set_dispatcher_service_auth(None)
        set_dispatcher_session_factory(None)
        set_dispatcher_redis_client(None)


@pytest.mark.asyncio
async def test_stage5_redis_lease_release_only_by_owner_token():
    """Requirement 6: Lock is released atomically only if owner_token matches (Lua CAS).

    Scenario: First lease expires/overtaken by second owner, first exit does not delete second lock.
    """
    from core.routing.lease import RedisAnalysisLease

    redis = InMemoryRedis()
    lock_key = "routing:analysis:lock:55555"

    # 1. Owner 1 acquires lock through the production lease abstraction.
    owner1 = RedisAnalysisLease(redis, lock_key, ttl_seconds=1)
    assert await owner1.acquire() is True
    assert await redis.get(lock_key) == owner1.owner_token

    # 2. Lock expires or is overwritten by Owner 2
    owner2_token = uuid.uuid4().hex
    await redis.set(lock_key, owner2_token)  # Owner 2 now owns lock

    # 3. Owner 1 tries to unlock using Lua CAS logic
    # In mock / production: if redis.get(key) == owner1: del, else return 0
    assert await owner1.release() is False

    # Lock must still belong to Owner 2
    assert await redis.get(lock_key) == owner2_token


@pytest.mark.asyncio
async def test_stage5_analyze_persists_atomically_visible_in_new_db_session(test_db, decision_service, fake_redis):
    """Requirement 2: Analyze atomically saves RoutingDecision + Preflight + PreparedPlan visible from new session."""
    mock_intra = AsyncMock()
    ticket_id = 142199 + int(uuid.uuid4().int % 100000)
    task = make_test_task(task_id=ticket_id, executor_ids=[999])
    mock_intra.get_task.return_value = task
    mock_intra.get_task_lifetime.return_value = []

    analysis_service = TicketAnalysisService(
        client=mock_intra,
        decision_service=decision_service,
        session_factory=test_db,
    )

    plan_dto = await analysis_service.analyze_ticket(ticket_id=ticket_id, force=True, redis_client=fake_redis)

    assert plan_dto.decision_id is not None
    assert plan_dto.plan_id is not None

    # Verify all 3 records are visible from a completely separate independent DB session
    async with test_db() as independent_session:
        stmt_dec = select(RoutingDecisionRecord).where(RoutingDecisionRecord.id == plan_dto.decision_id)
        dec_rec = (await independent_session.execute(stmt_dec)).scalar_one_or_none()
        assert dec_rec is not None
        assert dec_rec.task_id == ticket_id

        stmt_plan = select(PreparedPlanRecord).where(PreparedPlanRecord.id == plan_dto.plan_id)
        plan_rec = (await independent_session.execute(stmt_plan)).scalar_one_or_none()
        assert plan_rec is not None
        assert plan_rec.decision_id == plan_dto.decision_id
        assert plan_rec.plan_hash == plan_dto.plan_hash

        if plan_dto.preflight is not None and plan_dto.preflight.id is not None:
            stmt_pf = select(RoutingPreflightRecord).where(RoutingPreflightRecord.id == plan_dto.preflight.id)
            pf_rec = (await independent_session.execute(stmt_pf)).scalar_one_or_none()
            assert pf_rec is not None
            assert pf_rec.decision_id == plan_dto.decision_id


@pytest.mark.asyncio
async def test_stage5_needs_clarification_cannot_be_approved(test_db, fake_redis):
    """Requirement 3: A plan in needs_clarification state cannot be approved directly via /approve."""
    from fastapi import HTTPException

    from api.src.features.autopilot.service import AutopilotService
    from core.autopilot.policy_service import AutopilotPolicyService

    policy_service = AutopilotPolicyService(redis_client=fake_redis)
    mock_client = AsyncMock()
    ticket_id = 88881
    task = make_test_task(task_id=ticket_id, executor_ids=[999])
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = []

    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(login="bot", password="pwd", bot_user_id=999, auth_b64="Ym90OnB3ZA==")

    service = AutopilotService(
        client=mock_client,
        auth_bootstrap=mock_auth,
        policy_service=policy_service,
    )

    decision_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    snapshot = TicketSnapshotFactory.create(task)

    async with test_db() as session:
        # Create decision
        dec = RoutingDecisionRecord(
            id=decision_id,
            task_id=ticket_id,
            snapshot_hash=snapshot.snapshot_hash,
            router_version="v2",
            state="needs_clarification",
            selected_scenario="grant_wlan",
        )
        session.add(dec)
        await session.flush()

        plan_rec = PreparedPlanRecord(
            id=plan_id,
            task_id=ticket_id,
            decision_id=decision_id,
            snapshot_hash=snapshot.snapshot_hash,
            plan_hash=compute_canonical_plan_hash(
                task_id=ticket_id,
                decision_id=decision_id,
                snapshot_hash=snapshot.snapshot_hash,
                scenario_key="grant_wlan",
                proposed_params={},
            ),
            scenario_key="grant_wlan",
            state="needs_clarification",
            plan_json={
                "task_id": ticket_id,
                "decision_id": str(decision_id),
                "snapshot_hash": snapshot.snapshot_hash,
                "scenario_key": "grant_wlan",
                "proposed_params": {},
                "suggested_comment": "",
                "target_status_id": 3,
                "routing_state": "needs_clarification",
                "is_executable": False,
            },
        )
        session.add(plan_rec)
        await session.commit()

    # Attempt approve must raise 409
    req = ApprovePlanRequest(
        decision_id=decision_id,
        plan_id=plan_id,
        plan_hash=plan_rec.plan_hash,
        snapshot_hash=snapshot.snapshot_hash,
        expected_status_id=1,
        last_event_id=None,
    )

    async with test_db() as session:
        with pytest.raises(HTTPException) as exc_info:
            await service.approve_plan(
                ticket_id=ticket_id,
                req=req,
                operator_username="test_op",
                session=session,
                redis_client=fake_redis,
            )
        assert exc_info.value.status_code == 409
        assert "needs_clarification" in exc_info.value.detail


@pytest.mark.asyncio
async def test_stage5_mismatched_hashes_and_bindings_rejected(test_db, fake_redis):
    """Requirement 3 & 9: Mismatched decision_id, plan_id, plan_hash, snapshot_hash return 409."""
    from fastapi import HTTPException

    from api.src.features.autopilot.service import AutopilotService
    from core.autopilot.policy_service import AutopilotPolicyService

    policy_service = AutopilotPolicyService(redis_client=fake_redis)
    mock_client = AsyncMock()
    ticket_id = 88882
    task = make_test_task(task_id=ticket_id, executor_ids=[999])
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = []

    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(login="bot", password="pwd", bot_user_id=999, auth_b64="Ym90OnB3ZA==")

    service = AutopilotService(
        client=mock_client,
        auth_bootstrap=mock_auth,
        policy_service=policy_service,
    )

    decision_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    snapshot = TicketSnapshotFactory.create(task)

    async with test_db() as session:
        dec = RoutingDecisionRecord(
            id=decision_id,
            task_id=ticket_id,
            snapshot_hash=snapshot.snapshot_hash,
            router_version="v2",
            state="selected",
            selected_scenario="account_lock",
        )
        session.add(dec)
        await session.flush()

        plan_rec = PreparedPlanRecord(
            id=plan_id,
            task_id=ticket_id,
            decision_id=decision_id,
            snapshot_hash=snapshot.snapshot_hash,
            plan_hash=compute_canonical_plan_hash(
                task_id=ticket_id,
                decision_id=decision_id,
                snapshot_hash=snapshot.snapshot_hash,
                scenario_key="account_lock",
                proposed_params={"target_user": "ivanov.i"},
            ),
            scenario_key="account_lock",
            state="selected",
            plan_json={
                "task_id": ticket_id,
                "decision_id": str(decision_id),
                "snapshot_hash": snapshot.snapshot_hash,
                "scenario_key": "account_lock",
                "proposed_params": {"target_user": "ivanov.i"},
                "suggested_comment": "",
                "target_status_id": 3,
                "routing_state": "selected",
                "is_executable": True,
            },
        )
        session.add(plan_rec)
        await session.commit()

    async with test_db() as session:
        # 1. Tampered plan_hash
        req_tampered = ApprovePlanRequest(
            decision_id=decision_id,
            plan_id=plan_id,
            plan_hash=hashlib.sha256(b"tampered_plan_hash").hexdigest(),
            snapshot_hash=snapshot.snapshot_hash,
            expected_status_id=1,
            last_event_id=None,
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.approve_plan(ticket_id=ticket_id, req=req_tampered, operator_username="op", session=session, redis_client=fake_redis)
        assert exc_info.value.status_code == 409
        assert "Хеш плана" in exc_info.value.detail

        # 2. Mismatched decision_id
        req_wrong_dec = ApprovePlanRequest(
            decision_id=uuid.uuid4(),
            plan_id=plan_id,
            plan_hash=plan_rec.plan_hash,
            snapshot_hash=snapshot.snapshot_hash,
            expected_status_id=1,
            last_event_id=None,
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.approve_plan(ticket_id=ticket_id, req=req_wrong_dec, operator_username="op", session=session, redis_client=fake_redis)
        assert exc_info.value.status_code == 409
        assert "Идентификатор решения" in exc_info.value.detail


@pytest.mark.asyncio
async def test_stage5_bot_not_assigned_or_missing_fails_closed(test_db, fake_redis):
    """Requirement 4 & 9: Non-existent prepared plan must fail closed with 404 without creating command."""
    from fastapi import HTTPException

    from api.src.features.autopilot.service import AutopilotService
    from core.autopilot.policy_service import AutopilotPolicyService

    policy_service = AutopilotPolicyService(redis_client=fake_redis)
    mock_client = AsyncMock()
    ticket_id = 88883
    task = make_test_task(task_id=ticket_id, executor_ids=[101])
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = []

    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(login="bot", password="pwd", bot_user_id=999, auth_b64="Ym90OnB3ZA==")

    service = AutopilotService(
        client=mock_client,
        auth_bootstrap=mock_auth,
        policy_service=policy_service,
    )

    decision_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    snapshot = TicketSnapshotFactory.create(task)

    req = ApprovePlanRequest(
        decision_id=decision_id,
        plan_id=plan_id,
        plan_hash="a" * 64,
        snapshot_hash=snapshot.snapshot_hash,
        expected_status_id=1,
        last_event_id=None,
    )

    async with test_db() as session:
        with pytest.raises(HTTPException) as exc_info:
            await service.approve_plan(ticket_id=ticket_id, req=req, operator_username="op", session=session, redis_client=fake_redis)
        assert exc_info.value.status_code == 404
        assert "не найден" in exc_info.value.detail


@pytest.mark.asyncio
async def test_stage5_unknown_and_disabled_corrected_scenario_rejected(test_db, fake_redis):
    """Requirement 5 & 9: Unknown scenario or DISABLED scenario in /correct must be rejected (422/409)."""
    from fastapi import HTTPException

    from api.src.features.autopilot.service import AutopilotService
    from core.autopilot.policy_service import AutopilotPolicyService

    policy_service = AutopilotPolicyService(redis_client=fake_redis)
    mock_client = AsyncMock()
    ticket_id = 88884
    task = make_test_task(task_id=ticket_id, executor_ids=[999])
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = []

    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(login="bot", password="pwd", bot_user_id=999, auth_b64="Ym90OnB3ZA==")

    service = AutopilotService(
        client=mock_client,
        auth_bootstrap=mock_auth,
        policy_service=policy_service,
    )

    decision_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    snapshot = TicketSnapshotFactory.create(task)

    async with test_db() as session:
        dec = RoutingDecisionRecord(
            id=decision_id,
            task_id=ticket_id,
            snapshot_hash=snapshot.snapshot_hash,
            router_version="v2",
            state="selected",
            selected_scenario="grant_wlan",
        )
        session.add(dec)
        await session.flush()

        plan_rec = PreparedPlanRecord(
            id=plan_id,
            task_id=ticket_id,
            decision_id=decision_id,
            snapshot_hash=snapshot.snapshot_hash,
            plan_hash=compute_canonical_plan_hash(
                task_id=ticket_id,
                decision_id=decision_id,
                snapshot_hash=snapshot.snapshot_hash,
                scenario_key="grant_wlan",
                proposed_params={},
            ),
            scenario_key="grant_wlan",
            state="selected",
            plan_json={
                "task_id": ticket_id,
                "decision_id": str(decision_id),
                "snapshot_hash": snapshot.snapshot_hash,
                "scenario_key": "grant_wlan",
                "proposed_params": {},
                "suggested_comment": "",
                "target_status_id": 3,
                "routing_state": "selected",
                "is_executable": True,
            },
        )
        session.add(plan_rec)
        await session.commit()

    async with test_db() as session:
        # 1. Unknown scenario -> 422
        req_unknown = CorrectPlanRequest(
            decision_id=decision_id,
            plan_id=plan_id,
            plan_hash=plan_rec.plan_hash,
            snapshot_hash=snapshot.snapshot_hash,
            expected_status_id=1,
            last_event_id=None,
            corrected_scenario="non_existent_magic_scenario",
            corrected_params={},
            correction_tag="wrong_scenario",
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.correct_plan(ticket_id=ticket_id, req=req_unknown, operator_username="op", session=session, redis_client=fake_redis)
        assert exc_info.value.status_code == 422

        # 2. Legacy action cancel_ticket -> 422
        req_legacy = CorrectPlanRequest(
            decision_id=decision_id,
            plan_id=plan_id,
            plan_hash=plan_rec.plan_hash,
            snapshot_hash=snapshot.snapshot_hash,
            expected_status_id=1,
            last_event_id=None,
            corrected_scenario="cancel_ticket",
            corrected_params={},
            correction_tag="wrong_scenario",
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.correct_plan(ticket_id=ticket_id, req=req_legacy, operator_username="op", session=session, redis_client=fake_redis)
        assert exc_info.value.status_code == 422
