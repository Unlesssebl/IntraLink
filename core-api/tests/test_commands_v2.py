import datetime as dt
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.database.db import (
    ActionPolicyRecord,
    AsyncSessionLocal,
    CommandApproval,
    CommandAttempt,
    CommandEvent,
    CommandInbox,
    CommandOutbox,
    CommandRecord,
    Principal,
    PrincipalRole,
    TelegramLink,
    User,
    init_db,
)
from app.services.actions.policy import AUTO_ELIGIBLE_ACTIONS, AUTO_RETRY_ELIGIBLE_ACTIONS, PolicyEngine
from app.services.actions.registry import PolicyMode
from app.services.command_outbox import reconcile_expired_leases
from app.services.command_service import CommandService
from app.config import settings
from app.main import app
from app.services.identity import create_service_credential, ensure_rbac_catalog


@pytest_asyncio.fixture(autouse=True)
async def clean_command_tables():
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
async def test_unknown_action_is_rejected_before_policy_lookup():
    mode, allowed, reason = await PolicyEngine().evaluate_execution_mode("missing_action")
    assert mode == "disabled"
    assert allowed is False
    assert "Неизвестное действие" in reason


@pytest.mark.asyncio
async def test_safe_command_is_transactionally_queued_with_outbox():
    async with AsyncSessionLocal() as db:
        command, duplicate = await CommandService(db).create(
            action="diagnose_host",
            target={"host": "PC-001"},
            parameters={},
            idempotency_key="diagnose-PC-001-v1",
            initiator="operator",
            source="test",
            priority=5,
        )
        assert duplicate is False
        assert command.status == "queued"
        assert await db.scalar(
            select(func.count(CommandOutbox.id)).where(CommandOutbox.command_id == command.id)
        ) == 1


@pytest.mark.asyncio
async def test_state_changing_command_waits_without_outbox_until_approval():
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="install_printer",
            target={"pc_name": "PC-001"},
            parameters={"printer_name": "PRN-01"},
            idempotency_key="printer-PC-001-PRN-01",
            initiator="operator",
            source="test",
            priority=5,
        )
        assert command.status == "awaiting_approval"
        assert await db.scalar(
            select(func.count(CommandOutbox.id)).where(CommandOutbox.command_id == command.id)
        ) == 0

        command = await service.approve(
            command.id, decision="approve", reason=None, operator="operator"
        )
        assert command.status == "queued"
        assert await db.scalar(
            select(func.count(CommandOutbox.id)).where(CommandOutbox.command_id == command.id)
        ) == 1


@pytest.mark.asyncio
async def test_idempotency_key_rejects_a_different_request():
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        first, _ = await service.create(
            action="diagnose_host",
            target={"host": "PC-001"},
            parameters={},
            idempotency_key="same-request-key",
            initiator="operator",
            source="test",
            priority=5,
        )
        duplicate, is_duplicate = await service.create(
            action="diagnose_host",
            target={"host": "PC-001"},
            parameters={},
            idempotency_key="same-request-key",
            initiator="operator",
            source="test",
            priority=5,
        )
        assert duplicate.id == first.id
        assert is_duplicate is True

        with pytest.raises(HTTPException) as exc:
            await service.create(
                action="diagnose_host",
                target={"host": "PC-002"},
                parameters={},
                idempotency_key="same-request-key",
                initiator="operator",
                source="test",
                priority=5,
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_expired_running_claim_moves_to_needs_review_without_reexecution():
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="diagnose_host",
            target={"host": "PC-LEASE"},
            parameters={},
            idempotency_key="expired-claim-test",
            initiator="operator",
            source="test",
            priority=5,
        )
        await service.claim(
            command.id,
            worker_id="worker-1",
            message_id="lease-1",
        )
        command.lease_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await service.claim(
                command.id,
                worker_id="worker-2",
                message_id="lease-2",
            )

        assert exc.value.status_code == 409
        assert exc.value.detail["command_status"] == "needs_review"
        persisted = await db.get(CommandRecord, command.id)
        assert persisted is not None
        assert persisted.status == "needs_review"
        assert persisted.error_message == "claim_lease_expired_result_unknown"


@pytest.mark.asyncio
async def test_claim_token_guards_final_result():
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="diagnose_host",
            target={"host": "PC-001"},
            parameters={},
            idempotency_key="claim-test-key",
            initiator="operator",
            source="test",
            priority=5,
        )
        claim = await service.claim(command.id, worker_id="worker-1")
        command_id = command.id
        assert claim.command.status == "running"

        with pytest.raises(HTTPException) as exc:
            await service.finish(
                command_id,
                claim_token="x" * 32,
                outcome="succeeded",
                result={},
                error_message=None,
                worker_id="worker-1",
            )
        assert exc.value.status_code == 409
        await db.rollback()

        finished = await service.finish(
            command_id,
            claim_token=claim.token,
            outcome="succeeded",
            result={"online": True},
            error_message=None,
            worker_id="worker-1",
        )
        assert finished.status == "succeeded"
        assert finished.result_json == {"online": True}


@pytest.mark.asyncio
async def test_claim_records_transport_message_and_rejects_duplicate():
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="diagnose_host",
            target={"host": "PC-002"},
            parameters={},
            idempotency_key="inbox-message-test",
            initiator="operator",
            source="test",
            priority=5,
        )
        await service.claim(
            command.id,
            worker_id="worker-1",
            message_id="100-1",
            outbox_id=None,
        )
        command_id = command.id
        assert await db.scalar(
            select(func.count(CommandInbox.id)).where(CommandInbox.command_id == command_id)
        ) == 1
        await db.rollback()

        with pytest.raises(HTTPException) as exc:
            await service.claim(
                    command_id,
                worker_id="worker-1",
                message_id="100-1",
                outbox_id=None,
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_safe_failure_is_requeued_with_bounded_retry():
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="diagnose_host",
            target={"host": "PC-003"},
            parameters={},
            idempotency_key="retry-test-key",
            initiator="operator",
            source="test",
            priority=5,
        )
        claim = await service.claim(command.id, worker_id="worker-1")
        retried = await service.finish(
            command.id,
            claim_token=claim.token,
            outcome="failed",
            result={},
            error_message="temporary failure",
            worker_id="worker-1",
        )
        assert retried.status == "queued"
        assert retried.completed_at is None
        assert await db.scalar(
            select(func.count(CommandOutbox.id)).where(CommandOutbox.command_id == command.id)
        ) == 2


@pytest.mark.asyncio
async def test_queued_command_is_cancelled_if_policy_is_disabled_before_claim():
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="diagnose_host",
            target={"host": "PC-POLICY"},
            parameters={},
            idempotency_key="policy-disabled-before-claim",
            initiator="operator",
            source="test",
            priority=5,
        )
        db.add(
            ActionPolicyRecord(
                action="diagnose_host",
                mode="disabled",
                updated_by="admin:test",
            )
        )
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await service.claim(command.id, worker_id="worker-1")
        assert exc.value.status_code == 409
        await db.refresh(command)
        assert command.status == "cancelled"
        assert command.error_message == "action_policy_disabled_before_execution"


@pytest.mark.asyncio
async def test_unimplemented_action_is_rejected_before_queueing():
    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await CommandService(db).create(
                action="reset_password",
                target={"username": "user1"},
                parameters={},
                idempotency_key="unsupported-action-test",
                initiator="operator",
                source="test",
                priority=5,
            )
        assert exc.value.status_code == 501


@pytest.mark.asyncio
async def test_telegram_approval_requires_registered_operator():
    async with AsyncSessionLocal() as db:
        command, _ = await CommandService(db).create(
            action="install_printer",
            target={"pc_name": "PC-004"},
            parameters={"printer_name": "PRN-04"},
            idempotency_key="telegram-approval-test",
            initiator="bot_or_cli",
            source="telegram",
            priority=5,
        )
        command_id = command.id

    headers = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(
            f"/api/v2/commands/{command_id}/approval/telegram/challenge",
            headers=headers,
            json={"tg_user_id": 10001},
        )
        assert denied.status_code == 403

        async with AsyncSessionLocal() as db:
            await ensure_rbac_catalog(db, commit=False)
            principal = Principal(
                type="human", subject="operator.test", display_name="Operator Test", status="active"
            )
            db.add(principal)
            await db.flush()
            db.add(PrincipalRole(principal_id=principal.id, role_name="helpdesk_operator"))
            db.add(TelegramLink(
                tg_user_id=10001, principal_id=principal.id, status="verified"
            ))
            await db.commit()

        challenge = await client.post(
            f"/api/v2/commands/{command_id}/approval/telegram/challenge",
            headers=headers,
            json={"tg_user_id": 10001},
        )
        assert challenge.status_code == 200
        approved = await client.post(
            f"/api/v2/commands/{command_id}/approval/telegram",
            headers=headers,
            json={
                "decision": "approve",
                "challenge_token": challenge.json()["challenge_token"],
            },
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_reconcile_expired_lease_mutating_command_moves_to_needs_review_without_retry():
    """При истечении lease у mutating-команды (install_printer) она переходит в needs_review без ретрая."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="install_printer",
            target={"pc_name": "PC-EXP-01"},
            parameters={"printer_name": "PRN-01"},
            idempotency_key="lease-expire-printer-01",
            initiator="operator",
            source="test",
            priority=5,
        )
        await service.approve(command.id, decision="approve", reason="ok", operator="operator")
        claim = await service.claim(command.id, worker_id="worker-printer-1")
        assert claim.command.status == "running"

        # Искусственно состариваем lease_expires_at
        command.lease_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        await db.commit()

    # Запускаем фоновый цикл reconcile_expired_leases
    reconciled = await reconcile_expired_leases()
    assert reconciled == 1

    async with AsyncSessionLocal() as db:
        updated = await db.get(CommandRecord, command.id)
        assert updated is not None
        assert updated.status == "needs_review"
        assert updated.completed_at is not None
        assert "lease expired" in (updated.error_message or "").lower()

        # Проверяем, что повторной Outbox записи НЕ создано (только 1 исходная после approval)
        outbox_count = await db.scalar(
            select(func.count(CommandOutbox.id)).where(CommandOutbox.command_id == command.id)
        )
        assert outbox_count == 1

        # Проверяем запись события lease_expired_needs_review
        event = await db.scalar(
            select(CommandEvent)
            .where(
                CommandEvent.command_id == command.id,
                CommandEvent.event_type == "lease_expired_needs_review",
            )
        )
        assert event is not None


@pytest.mark.asyncio
async def test_reconcile_expired_lease_safe_command_is_requeued():
    """При истечении lease у read-only команды (diagnose_host) она ретраится с экспоненциальной задержкой."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="diagnose_host",
            target={"host": "PC-EXP-SAFE"},
            parameters={},
            idempotency_key="lease-expire-diagnose-01",
            initiator="operator",
            source="test",
            priority=5,
        )
        await service.claim(command.id, worker_id="worker-diag-1")
        command.lease_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        await db.commit()

    reconciled = await reconcile_expired_leases()
    assert reconciled == 1

    async with AsyncSessionLocal() as db:
        updated = await db.get(CommandRecord, command.id)
        assert updated is not None
        assert updated.status == "queued"
        assert updated.completed_at is None

        # Проверяем, что создана вторая Outbox запись (первая при create, вторая на повтор)
        outbox_count = await db.scalar(
            select(func.count(CommandOutbox.id)).where(CommandOutbox.command_id == command.id)
        )
        assert outbox_count == 2

        event = await db.scalar(
            select(CommandEvent)
            .where(
                CommandEvent.command_id == command.id,
                CommandEvent.event_type == "lease_expired_requeued",
            )
        )
        assert event is not None


@pytest.mark.asyncio
async def test_renew_lease_method_logic():
    """Прямой тест метода renew_lease в CommandService."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="diagnose_host",
            target={"host": "PC-RENEW-01"},
            parameters={},
            idempotency_key="renew-lease-method-test",
            initiator="operator",
            source="test",
            priority=5,
        )
        claim = await service.claim(command.id, worker_id="worker-1", lease_seconds=60)
        orig_expires = claim.command.lease_expires_at
        assert orig_expires is not None

        # 1. Успешное продление
        renewed, ttl = await service.renew_lease(
            command.id,
            worker_id="worker-1",
            claim_token=claim.token,
            lease_seconds=180,
        )
        assert ttl == 180
        assert renewed.lease_expires_at > orig_expires

        # 2. Неверный claim token -> 409
        with pytest.raises(HTTPException) as exc:
            await service.renew_lease(
                command.id,
                worker_id="worker-1",
                claim_token="wrong-token-" + "x" * 25,
                lease_seconds=60,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "stale_or_invalid_claim_token"

        # 3. Истекший lease -> 409
        renewed.lease_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await service.renew_lease(
                command.id,
                worker_id="worker-1",
                claim_token=claim.token,
                lease_seconds=60,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "claim_lease_expired"


@pytest.mark.asyncio
async def test_heartbeat_http_endpoint_with_service_credentials():
    """Тест HTTP эндпоинта POST /api/v2/commands/{id}/heartbeat с сервисными учетными данными."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="diagnose_host",
            target={"host": "PC-HEARTBEAT-HTTP"},
            parameters={},
            idempotency_key="heartbeat-http-test",
            initiator="operator",
            source="test",
            priority=5,
        )
        claim = await service.claim(command.id, worker_id="worker-win-01", lease_seconds=60)
        command_id = command.id
        token = claim.token

        # Создаем сервисный аккаунт с правом claim:windows
        _principal, credential, secret = await create_service_credential(
            db,
            subject="service-win-worker",
            display_name="Windows Execution Worker",
            scopes={"command:claim:windows", "command:finish:windows"},
        )
        key_id = credential.key_id

    headers = {
        "X-Service-Key-Id": key_id,
        "X-Service-Secret": secret,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Успешный heartbeat
        resp = await client.post(
            f"/api/v2/commands/{command_id}/heartbeat",
            headers=headers,
            json={
                "worker_id": "worker-win-01",
                "claim_token": token,
                "lease_seconds": 120,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["command_id"] == str(command_id)
        assert data["status"] == "running"
        assert data["lease_ttl_seconds"] == 120
        assert "lease_expires_at" in data

        # Невалидный токен -> 409
        resp_invalid = await client.post(
            f"/api/v2/commands/{command_id}/heartbeat",
            headers=headers,
            json={
                "worker_id": "worker-win-01",
                "claim_token": "invalid-token-" + "y" * 25,
                "lease_seconds": 120,
            },
        )
        assert resp_invalid.status_code == 409

        # Без авторизации -> 401
        resp_unauth = await client.post(
            f"/api/v2/commands/{command_id}/heartbeat",
            json={
                "worker_id": "worker-win-01",
                "claim_token": token,
                "lease_seconds": 120,
            },
        )
        assert resp_unauth.status_code == 401


@pytest.mark.asyncio
async def test_policy_engine_rejects_auto_for_mutating_actions():
    """PolicyEngine блокирует установку AUTO для изменяющих действий (install_printer, apply_triage)."""
    engine = PolicyEngine()

    assert "install_printer" not in AUTO_ELIGIBLE_ACTIONS
    assert "apply_triage" not in AUTO_ELIGIBLE_ACTIONS
    assert AUTO_ELIGIBLE_ACTIONS == frozenset({"diagnose_host", "rag_sync"})
    assert AUTO_RETRY_ELIGIBLE_ACTIONS == frozenset({"diagnose_host", "rag_sync"})

    with pytest.raises(ValueError) as exc:
        await engine.set_action_policy("install_printer", PolicyMode.AUTO)
    assert "не входит в allowlist безопасной автономности" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        await engine.set_action_policy("apply_triage", PolicyMode.AUTO)
    assert "не входит в allowlist безопасной автономности" in str(exc.value)


@pytest.mark.asyncio
async def test_plan_hash_generation_and_freeze_event():
    """При создании команды вычисляется plan_hash и фиксируется событие plan_frozen."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="install_printer",
            target={"pc_name": "PC-001"},
            parameters={"printer_name": "PRN-01"},
            idempotency_key="printer-plan-hash-test-1",
            initiator="operator",
            source="test",
            priority=5,
        )
        assert command.plan_hash is not None
        assert len(command.plan_hash) == 64
        assert command.plan_expires_at is not None
        assert command.preflight_evidence_json == {}

        events = list((await db.scalars(
            select(CommandEvent).where(CommandEvent.command_id == command.id).order_by(CommandEvent.sequence)
        )).all())
        assert len(events) == 2
        assert events[0].event_type == "created"
        assert events[1].event_type == "plan_frozen"
        assert events[1].details_json["plan_hash"] == command.plan_hash
        assert events[1].details_json["version"] == 1


@pytest.mark.asyncio
async def test_approve_rejects_plan_drift_and_version_mismatch():
    """Approve отклоняет запрос при несовпадении expected_plan_hash или expected_version."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="install_printer",
            target={"pc_name": "PC-001"},
            parameters={"printer_name": "PRN-01"},
            idempotency_key="printer-plan-drift-test",
            initiator="operator",
            source="test",
            priority=5,
        )

        # Неверная версия -> 409 plan_drift_detected
        with pytest.raises(HTTPException) as exc:
            await service.approve(
                command.id,
                decision="approve",
                reason="all good",
                operator="admin",
                expected_version=99,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["detail"] == "plan_drift_detected"

        # Неверный plan_hash -> 409 plan_drift_detected
        with pytest.raises(HTTPException) as exc:
            await service.approve(
                command.id,
                decision="approve",
                reason="all good",
                operator="admin",
                expected_plan_hash="0" * 64,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["detail"] == "plan_drift_detected"

        # Корректный plan_hash и version -> Успешно
        approved = await service.approve(
            command.id,
            decision="approve",
            reason="verified",
            operator="admin",
            expected_plan_hash=command.plan_hash,
            expected_version=1,
        )
        assert approved.status == "queued"


@pytest.mark.asyncio
async def test_approve_rejects_expired_plan():
    """Approve отклоняет согласование, если истек срок действия плана (plan_expires_at)."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="install_printer",
            target={"pc_name": "PC-001"},
            parameters={"printer_name": "PRN-01"},
            idempotency_key="printer-plan-expired-test",
            initiator="operator",
            source="test",
            priority=5,
        )
        # Имитируем просроченный план
        command.plan_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await service.approve(
                command.id,
                decision="approve",
                reason="late approval",
                operator="admin",
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["detail"] == "plan_expired"


@pytest.mark.asyncio
async def test_record_preflight_updates_version_and_plan_hash():
    """record_preflight инкрементирует версию, сохраняет evidence и обновляет plan_hash."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="install_printer",
            target={"pc_name": "PC-001"},
            parameters={"printer_name": "PRN-01"},
            idempotency_key="printer-record-preflight-test",
            initiator="operator",
            source="test",
            priority=5,
        )
        old_hash = command.plan_hash
        assert command.version == 1

        evidence = {"smb_available": True, "driver_found": True, "os_version": "Windows 10"}
        updated = await service.record_preflight(
            command.id,
            worker_id="worker-01",
            claim_token="preflight-token-ignored-if-awaiting",
            evidence=evidence,
            ttl_seconds=3600,
        )
        assert updated.version == 2
        assert updated.preflight_evidence_json == evidence
        assert updated.plan_hash != old_hash

        # Теперь согласование требует новый plan_hash и version 2
        approved = await service.approve(
            command.id,
            decision="approve",
            reason="preflight passed",
            operator="admin",
            expected_plan_hash=updated.plan_hash,
            expected_version=2,
        )
        assert approved.status == "queued"


@pytest.mark.asyncio
async def test_finish_mutating_command_enforces_execution_evidence_gate():
    """Завершение мутирующей команды в succeeded без доказательств верификации блокируется (fail-closed)."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="install_printer",
            target={"pc_name": "PC-001"},
            parameters={"printer_name": "PRN-01"},
            idempotency_key="printer-evidence-gate-test",
            initiator="operator",
            source="test",
            priority=5,
        )
        approved = await service.approve(
            command.id, decision="approve", reason="ok", operator="admin"
        )
        claim = await service.claim(approved.id, worker_id="worker-01", message_id="msg-ev-1")

        # Попытка финишировать без evidence в result и без события верификации -> 409
        with pytest.raises(HTTPException) as exc:
            await service.finish(
                claim.command.id,
                claim_token=claim.token,
                outcome="succeeded",
                result={"status": "installed_maybe"},
                error_message=None,
                worker_id="worker-01",
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["detail"] == "missing_execution_evidence"

        # Финиш с verified: True в result -> Успешно
        finished = await service.finish(
            claim.command.id,
            claim_token=claim.token,
            outcome="succeeded",
            result={"status": "installed", "verified": True},
            error_message=None,
            worker_id="worker-01",
        )
        assert finished.status == "succeeded"


@pytest.mark.asyncio
async def test_api_v2_preflight_and_phase_endpoints():
    """Эндпоинты preflight и phase работают через HTTP API с сервисными учетными данными."""
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        command, _ = await service.create(
            action="install_printer",
            target={"pc_name": "PC-001"},
            parameters={"printer_name": "PRN-01"},
            idempotency_key="printer-api-preflight-test",
            initiator="operator",
            source="test",
            priority=5,
        )
        command_id = command.id

        _principal, credential, secret = await create_service_credential(
            db,
            subject="service-win-worker-pf",
            display_name="Windows Execution Worker",
            scopes={"command:claim:windows", "command:finish:windows"},
        )
        key_id = credential.key_id

    headers = {
        "X-Service-Key-Id": key_id,
        "X-Service-Secret": secret,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Preflight
        resp_pf = await client.post(
            f"/api/v2/commands/{command_id}/preflight",
            headers=headers,
            json={
                "worker_id": "worker-win-01",
                "claim_token": "mock-token-for-awaiting-" + "x" * 20,
                "evidence": {"dns": True, "ping": True},
                "ttl_seconds": 3600,
            },
        )
        assert resp_pf.status_code == 200, resp_pf.text
        data_pf = resp_pf.json()
        assert data_pf["version"] == 2
        assert data_pf["preflight_evidence"] == {"dns": True, "ping": True}
        assert data_pf["plan_hash"] is not None

