"""Final Stage 5 repair-gate regressions."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api.src.features.autopilot.schemas import ApprovePlanRequest, CorrectPlanRequest
from api.src.features.autopilot.service import AutopilotService
from core.autopilot.dto import AgentPlanDTO, AutopilotPolicyDTO, PreflightResultDTO
from core.database.models import PreparedPlanRecord
from core.intraservice.auth import ServiceAuthCredentials
from core.intraservice.dto import TaskDTO
from core.routing.analysis_service import _SCENARIO_META
from core.routing.lease import (
    AnalysisLeaseBackendError,
    AnalysisLeaseOwnershipLost,
    RedisAnalysisLease,
)
from core.routing.preflight import compute_canonical_params_hash, compute_canonical_plan_hash
from core.routing.snapshot import TicketSnapshotFactory
from worker.src.tasks.ad_actions import reset_ad_password_task


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


def _task(task_id: int = 951001) -> TaskDTO:
    return TaskDTO(
        id=task_id,
        name="Перенаправление заявки",
        description="Заявка создана не в том разделе",
        status_id=1,
        status_name="Новая",
        service_id=999,
        service_name="Другое",
        created="2026-09-26T10:00:00Z",
        creator_name="Тестовый пользователь",
        executor_ids="999",
    )


def _baseline_plan(task: TaskDTO, *, stored_task_id: Optional[int] = None) -> SimpleNamespace:
    snapshot = TicketSnapshotFactory.create(task)
    task_id = stored_task_id if stored_task_id is not None else task.id
    decision_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    payload = {
        "task_id": task_id,
        "decision_id": str(decision_id),
        "snapshot_hash": snapshot.snapshot_hash,
        "scenario_key": "service_redirect",
        "proposed_params": {},
        "suggested_comment": "",
        "target_status_id": 3,
        "routing_state": "selected",
        "is_executable": True,
    }
    plan_hash = compute_canonical_plan_hash(
        task_id=task_id,
        decision_id=decision_id,
        snapshot_hash=snapshot.snapshot_hash,
        scenario_key="service_redirect",
        proposed_params={},
    )
    return SimpleNamespace(
        id=plan_id,
        task_id=task_id,
        decision_id=decision_id,
        snapshot_hash=snapshot.snapshot_hash,
        plan_hash=plan_hash,
        scenario_key="service_redirect",
        plan_json=payload,
        state="selected",
        preflight_id=None,
    )


def _correct_request(task: TaskDTO, baseline: SimpleNamespace) -> CorrectPlanRequest:
    snapshot = TicketSnapshotFactory.create(task)
    return CorrectPlanRequest(
        decision_id=baseline.decision_id,
        plan_id=baseline.id,
        plan_hash=baseline.plan_hash,
        snapshot_hash=snapshot.snapshot_hash,
        expected_status_id=task.status_id,
        last_event_id=None,
        corrected_scenario="service_redirect",
        corrected_params={},
        correction_tag="repair_gate",
    )


def _correction_service(
    task: TaskDTO,
    baseline: SimpleNamespace,
    preflight_record: Optional[Any] = None,
) -> tuple[AutopilotService, AsyncMock, Any]:
    from core.autopilot.dto import ScenarioCatalogItemDTO
    client = AsyncMock()
    client.get_task.return_value = task
    client.get_task_lifetime.return_value = []
    auth = AsyncMock()
    auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        login="bot",
        password="test-only",
        bot_user_id=999,
        auth_b64="dGVzdA==",
    )
    policy = AsyncMock()
    policy.get_policy.return_value = AutopilotPolicyDTO(scenario_key="service_redirect", mode="ASSISTED")
    preflight = AsyncMock()
    catalog = AsyncMock()
    catalog.get_catalog.return_value = [
        ScenarioCatalogItemDTO(
            scenario_key="service_redirect",
            name="Перенаправление",
            description="Перенаправление заявки",
            is_enabled=True,
            policy_mode="ASSISTED",
        )
    ]
    service = AutopilotService(
        client=client,
        auth_bootstrap=auth,
        policy_service=policy,
        preflight_service=preflight,
        catalog_service=catalog,
    )
    service.fact_policy = MagicMock()
    service.fact_policy.check_readiness.return_value = ("ready", [])
    session = AsyncMock()

    async def _mock_execute(stmt):
        stmt_str = str(stmt).lower()
        if "routing_preflight" in stmt_str:
            return _ScalarResult(preflight_record)
        if "routing_feedback" in stmt_str:
            return _ScalarResult(None)
        if "command" in stmt_str:
            return _ScalarResult(None)
        if "prepared_plan" in stmt_str:
            return _ScalarResult(baseline)
        if "routing_decision" in stmt_str:
            return _ScalarResult(None)
        return _ScalarResult(baseline)

    session.execute.side_effect = _mock_execute
    session.add = MagicMock()
    return service, preflight, session


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["request_hash", "other_task", "tampered_json"])
async def test_correct_plan_rejects_invalid_baseline_before_side_effects(failure: str) -> None:
    task = _task()
    baseline = _baseline_plan(task, stored_task_id=task.id + 1 if failure == "other_task" else None)
    req = _correct_request(task, baseline)
    if failure == "request_hash":
        req.plan_hash = "f" * 64
    elif failure == "tampered_json":
        baseline.plan_json = {**baseline.plan_json, "proposed_params": {"tampered": True}}

    service, preflight, session = _correction_service(task, baseline)

    with pytest.raises(HTTPException) as exc_info:
        await service.correct_plan(ticket_id=task.id, req=req, session=session, operator_username="operator")

    assert exc_info.value.status_code in (400, 409)
    preflight.execute_preflight.assert_not_awaited()
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preflight_status", "preflight_id", "expires_at", "is_expired"),
    [
        ("degraded", uuid.uuid4(), datetime.now(UTC) + timedelta(minutes=1), False),
        ("passed", uuid.uuid4(), datetime.now(UTC) - timedelta(seconds=1), False),
        ("passed", None, datetime.now(UTC) + timedelta(minutes=1), False),
        ("mystery", uuid.uuid4(), datetime.now(UTC) + timedelta(minutes=1), False),
    ],
    ids=["degraded", "expired", "missing", "unknown"],
)
async def test_correct_plan_rejects_non_executable_preflight_without_writes(
    preflight_status: str,
    preflight_id: Optional[uuid.UUID],
    expires_at: datetime,
    is_expired: bool,
) -> None:
    task = _task()
    baseline = _baseline_plan(task)
    req = _correct_request(task, baseline)
    service, preflight, session = _correction_service(task, baseline)
    preflight.execute_preflight.return_value = PreflightResultDTO(
        id=preflight_id,
        status=preflight_status,
        scenario_key="service_redirect",
        params_hash="a" * 64,
        expires_at=expires_at,
        is_expired=is_expired,
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.correct_plan(ticket_id=task.id, req=req, session=session, operator_username="operator")

    assert exc_info.value.status_code == 409
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preflight_status", "has_preflight", "expires_at"),
    [
        ("degraded", True, datetime.now(UTC) + timedelta(minutes=1)),
        ("passed", True, datetime.now(UTC) - timedelta(seconds=1)),
        ("passed", False, datetime.now(UTC) + timedelta(minutes=1)),
        ("mystery", True, datetime.now(UTC) + timedelta(minutes=1)),
    ],
    ids=["degraded", "expired", "missing", "unknown"],
)
async def test_approve_plan_rejects_non_executable_preflight_without_command(
    preflight_status: str,
    has_preflight: bool,
    expires_at: datetime,
) -> None:
    task = _task()
    baseline = _baseline_plan(task)
    if has_preflight:
        baseline.preflight_id = uuid.uuid4()
    req = ApprovePlanRequest(
        decision_id=baseline.decision_id,
        plan_id=baseline.id,
        plan_hash=baseline.plan_hash,
        snapshot_hash=baseline.snapshot_hash,
        expected_status_id=task.status_id,
        last_event_id=None,
    )
    preflight_record = SimpleNamespace(
        id=baseline.preflight_id,
        decision_id=baseline.decision_id,
        snapshot_hash=baseline.snapshot_hash,
        scenario_key=baseline.scenario_key,
        params_hash=compute_canonical_params_hash({}),
        status=preflight_status,
        expires_at=expires_at,
        error_message=None,
    ) if has_preflight else None
    service, _, session = _correction_service(task, baseline, preflight_record)

    with pytest.raises(HTTPException) as exc_info:
        await service.approve_plan(ticket_id=task.id, req=req, session=session, operator_username="operator")

    assert exc_info.value.status_code == 409
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


class _LeaseRedis:
    def __init__(self) -> None:
        self.values: dict[str, tuple[str, float]] = {}
        self.fail_set = False
        self.renewals = 0

    def _current(self, key: str) -> Optional[str]:
        value = self.values.get(key)
        if value is None:
            return None
        token, expires_at = value
        if time.monotonic() >= expires_at:
            self.values.pop(key, None)
            return None
        return token

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int) -> bool:
        if self.fail_set:
            raise ConnectionError("redis unavailable")
        if nx and self._current(key) is not None:
            return False
        self.values[key] = (value, time.monotonic() + ex)
        return True

    async def get(self, key: str) -> Optional[str]:
        return self._current(key)

    async def eval(self, script: str, numkeys: int, key: str, *args: Any) -> int:
        del numkeys
        token = args[0]
        if self._current(key) != token:
            return 0
        if "expire" in script.lower():
            self.renewals += 1
            self.values[key] = (token, time.monotonic() + int(args[1]))
            return 1
        self.values.pop(key, None)
        return 1


@pytest.mark.asyncio
async def test_analysis_lease_redis_set_exception_is_explicit() -> None:
    redis = _LeaseRedis()
    redis.fail_set = True
    lease = RedisAnalysisLease(redis, "lease:set-error")

    with pytest.raises(AnalysisLeaseBackendError):
        await lease.acquire()


@pytest.mark.asyncio
async def test_analysis_lease_detects_ownership_loss_and_old_owner_cannot_release() -> None:
    redis = _LeaseRedis()
    lease = RedisAnalysisLease(redis, "lease:ownership", ttl_seconds=2)
    assert await lease.acquire() is True
    redis.values[lease.key] = ("new-owner", time.monotonic() + 2)

    with pytest.raises(AnalysisLeaseOwnershipLost):
        await lease.renew()
    assert await lease.release() is False
    assert await redis.get(lease.key) == "new-owner"


@pytest.mark.asyncio
async def test_analysis_lease_renews_and_blocks_parallel_past_initial_ttl() -> None:
    redis = _LeaseRedis()
    first = RedisAnalysisLease(
        redis,
        "lease:long-analysis",
        ttl_seconds=1,
        renewal_interval_seconds=0.15,
    )
    assert await first.acquire() is True
    first.start_renewal()
    try:
        await asyncio.sleep(1.2)
        second = RedisAnalysisLease(redis, first.key, ttl_seconds=1)
        assert await second.acquire() is False
        assert redis.renewals >= 2
        await first.ensure_owned()
    finally:
        await first.stop_renewal()
        await first.release()


@pytest.mark.asyncio
async def test_password_reset_artifacts_contain_no_plaintext_or_temporary_password_markers(caplog: pytest.LogCaptureFixture) -> None:
    secret = "TempPass123!"
    comment = _SCENARIO_META["ad_password_reset"]["comment"]
    plan = AgentPlanDTO(
        task_id=951002,
        scenario_key="ad_password_reset",
        scenario_name="Сброс пароля Active Directory",
        suggested_comment=comment,
    )
    plan_json = plan.model_dump_json()
    plan_record = PreparedPlanRecord(
        task_id=plan.task_id,
        decision_id=uuid.uuid4(),
        snapshot_hash="a" * 64,
        plan_hash="b" * 64,
        scenario_key=plan.scenario_key,
        plan_json=plan.model_dump(mode="json"),
        state="selected",
    )

    caplog.set_level(logging.INFO)
    taskiq_result = await reset_ad_password_task("ivanov.i")
    logging.getLogger("test.stage5.security").info("%s %s", comment, taskiq_result)
    artifacts = "\n".join(
        [
            plan_json,
            str(plan_record.plan_json),
            comment,  # public suggested comment
            f"Внутренняя запись: {comment}",
            str(taskiq_result),
            caplog.text,
        ]
    ).lower()

    assert secret.lower() not in artifacts
    assert "временный пароль" not in artifacts
    assert "временного пароля" not in artifacts
    assert comment == "Данные для входа размещены в защищённых полях заявки."
