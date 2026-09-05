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
from app.services.actions.policy import PolicyEngine
from app.services.command_service import CommandService
from app.config import settings
from app.main import app
from app.services.identity import ensure_rbac_catalog


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
