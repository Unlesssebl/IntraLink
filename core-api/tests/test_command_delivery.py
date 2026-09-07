"""Integration tests for verified create_user resolution delivery and credential lifecycle."""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database.db import (
    AsyncSessionLocal,
    CommandRecord,
    CommandSecretArtifact,
    ResolutionPolicy,
    ResponseTemplate,
)
from app.main import app
from app.services import crypto
from app.services.command_delivery import CommandDeliveryService
from app.services.command_secrets import CommandSecretService
from app.services.identity import create_service_credential


@pytest.fixture(autouse=True)
def setup_fernet(monkeypatch):
    monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))


async def _ensure_user_created_policy(db) -> tuple[ResponseTemplate, ResolutionPolicy]:
    template = await db.scalar(
        ResponseTemplate.__table__.select().where(ResponseTemplate.key == "user_created")
    )
    if template is None:
        template = ResponseTemplate(
            key="user_created",
            version=1,
            name="Создание учетной записи AD выполнено",
            template_text="Учетная запись создана.\nЛогин: {{ login }}\nПароль: {{ password }}",
            required_variables=["login", "password"],
            is_active=True,
            created_by="test",
        )
        db.add(template)
        await db.flush()

    policy = await db.scalar(
        ResolutionPolicy.__table__.select().where(ResolutionPolicy.outcome_key == "user_created")
    )
    if policy is None:
        policy = ResolutionPolicy(
            outcome_key="user_created",
            version=1,
            outcome_kind="resolution",
            template_id=template.id,
            target_status_id=29,
            status_name="Выполнена",
            expenses=10,
            risk_level=0,
            requires_approval=True,
            is_active=True,
            created_by="test",
        )
        db.add(policy)
        await db.flush()

    await db.commit()
    return template, policy


@pytest.mark.asyncio
async def test_successful_create_user_delivery():
    token = secrets.token_urlsafe(32)
    async with AsyncSessionLocal() as db:
        await _ensure_user_created_policy(db)
        command = CommandRecord(
            idempotency_key=f"delivery-test-{uuid.uuid4()}",
            request_hash="b" * 64,
            action="create_user",
            executor="windows",
            target_json={"task_id": 140001},
            params_json={"surname": "Иванов", "name": "Иван"},
            status="succeeded",
            result_json={
                "verified": True,
                "sam_account_name": "ivanov.i.i",
                "display_name": "Иванов Иван Иванович",
            },
            task_id=140001,
            initiator="operator",
            source="test",
            lease_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        db.add(command)
        await db.commit()
        await db.refresh(command)

        # Сохраняем временный пароль через secret service
        command.status = "running"
        await db.commit()
        secret_service = CommandSecretService(db)
        await secret_service.store(
            command.id,
            claim_token=token,
            name="temporary_password",
            value="SecurePass-999!",
            ttl_seconds=300,
        )
        command.status = "succeeded"
        await db.commit()

        delivery = CommandDeliveryService(db)

        with (
            patch(
                "app.services.intraservice.get_single_task",
                new=AsyncMock(return_value={"Id": 140001, "StatusId": 31}),
            ) as mock_get,
            patch(
                "app.services.intraservice.update_task_full",
                new=AsyncMock(return_value=True),
            ) as mock_update,
            patch(
                "app.services.intraservice.add_task_expenses",
                new=AsyncMock(return_value=True),
            ) as mock_expenses,
        ):
            meta = await delivery.deliver_create_user(
                command.id,
                actor="test-operator",
                service_auth_b64="test-auth",
            )

        # 1. Проверяем возвращенные метаданные (пароля нет в открытом виде!)
        assert meta["status"] == "delivered"
        assert meta["target_status_id"] == 29
        assert meta["template_key"] == "user_created"
        assert meta["consumed_at"] is not None
        assert "SecurePass-999!" not in str(meta)

        # 2. Проверяем вызовы IntraService
        mock_get.assert_awaited_once_with("test-auth", 140001)
        assert mock_update.await_count == 2  # Сначала статус 27, затем статус 29
        final_call = mock_update.await_args_list[1]
        assert final_call.kwargs["status_id"] == 29
        assert "ivanov.i.i" in final_call.kwargs["comment"]
        assert "SecurePass-999!" in final_call.kwargs["comment"]
        mock_expenses.assert_awaited_once()

        # 3. Проверяем, что артефакт стерт (wiped) и помечен как consumed
        persisted_artifact = await db.scalar(
            select(CommandSecretArtifact).where(
                CommandSecretArtifact.command_id == command.id
            )
        )
        assert persisted_artifact is not None
        assert persisted_artifact.consumed_at is not None
        assert persisted_artifact.encrypted_value == ""


@pytest.mark.asyncio
async def test_delivery_fails_on_verification_mismatch():
    token = secrets.token_urlsafe(32)
    async with AsyncSessionLocal() as db:
        await _ensure_user_created_policy(db)
        command = CommandRecord(
            idempotency_key=f"delivery-test-mismatch-{uuid.uuid4()}",
            request_hash="c" * 64,
            action="create_user",
            executor="windows",
            target_json={"task_id": 140002},
            params_json={"surname": "Иванов"},
            status="succeeded",
            result_json={
                "verified": False,  # Verification failed!
                "sam_account_name": "ivanov.i.i",
            },
            task_id=140002,
            initiator="operator",
            source="test",
            lease_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        db.add(command)
        await db.commit()
        await db.refresh(command)

        command.status = "running"
        await db.commit()
        await CommandSecretService(db).store(
            command.id,
            claim_token=token,
            name="temporary_password",
            value="SecurePass-999!",
            ttl_seconds=300,
        )
        command.status = "succeeded"
        await db.commit()

        delivery = CommandDeliveryService(db)

        with patch("app.services.intraservice.update_task_full") as mock_update:
            with pytest.raises(HTTPException) as exc:
                await delivery.deliver_create_user(
                    command.id,
                    actor="test-operator",
                    service_auth_b64="test-auth",
                )
            assert exc.value.status_code == 409
            assert "verification missing or failed" in exc.value.detail
            mock_update.assert_not_awaited()

        # Артефакт остался непотребленным
        persisted_artifact = await db.scalar(
            select(CommandSecretArtifact).where(
                CommandSecretArtifact.command_id == command.id
            )
        )
        assert persisted_artifact.consumed_at is None
        assert persisted_artifact.encrypted_value != ""


@pytest.mark.asyncio
async def test_delivery_fails_on_expired_or_consumed_artifact():
    token = secrets.token_urlsafe(32)
    async with AsyncSessionLocal() as db:
        await _ensure_user_created_policy(db)
        command = CommandRecord(
            idempotency_key=f"delivery-test-consumed-{uuid.uuid4()}",
            request_hash="d" * 64,
            action="create_user",
            executor="windows",
            target_json={"task_id": 140003},
            params_json={"surname": "Иванов"},
            status="succeeded",
            result_json={
                "verified": True,
                "sam_account_name": "ivanov.i.i",
            },
            task_id=140003,
            initiator="operator",
            source="test",
            lease_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        db.add(command)
        await db.commit()
        await db.refresh(command)

        command.status = "running"
        await db.commit()
        secret_service = CommandSecretService(db)
        artifact = await secret_service.store(
            command.id,
            claim_token=token,
            name="temporary_password",
            value="SecurePass-999!",
            ttl_seconds=300,
        )
        command.status = "succeeded"
        # Симулируем уже потребленный артефакт
        artifact.consumed_at = dt.datetime.now(dt.timezone.utc)
        await db.commit()

        delivery = CommandDeliveryService(db)
        with patch("app.services.intraservice.update_task_full") as mock_update:
            with pytest.raises(HTTPException) as exc:
                await delivery.deliver_create_user(
                    command.id,
                    actor="test-operator",
                    service_auth_b64="test-auth",
                )
            assert exc.value.status_code == 410
            mock_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_retains_artifact_on_intraservice_failure():
    token = secrets.token_urlsafe(32)
    async with AsyncSessionLocal() as db:
        await _ensure_user_created_policy(db)
        command = CommandRecord(
            idempotency_key=f"delivery-test-retryable-{uuid.uuid4()}",
            request_hash="e" * 64,
            action="create_user",
            executor="windows",
            target_json={"task_id": 140004},
            params_json={"surname": "Иванов"},
            status="succeeded",
            result_json={
                "verified": True,
                "sam_account_name": "ivanov.i.i",
            },
            task_id=140004,
            initiator="operator",
            source="test",
            lease_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        db.add(command)
        await db.commit()
        await db.refresh(command)

        command.status = "running"
        await db.commit()
        secret_service = CommandSecretService(db)
        await secret_service.store(
            command.id,
            claim_token=token,
            name="temporary_password",
            value="SecurePass-999!",
            ttl_seconds=300,
        )
        command.status = "succeeded"
        await db.commit()

        delivery = CommandDeliveryService(db)

        # Эмулируем ошибку IntraService при обновлении
        with (
            patch(
                "app.services.intraservice.get_single_task",
                new=AsyncMock(return_value={"Id": 140004, "StatusId": 27}),
            ),
            patch(
                "app.services.intraservice.update_task_full",
                new=AsyncMock(return_value=False),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await delivery.deliver_create_user(
                    command.id,
                    actor="test-operator",
                    service_auth_b64="test-auth",
                )
            assert exc.value.status_code == 502

        # ПРОВЕРЯЕМ: Артефакт НЕ стёрт и НЕ помечен как consumed! Пароль можно расшифровать при retry.
        persisted_artifact = await db.scalar(
            select(CommandSecretArtifact).where(
                CommandSecretArtifact.command_id == command.id
            )
        )
        assert persisted_artifact.consumed_at is None
        assert persisted_artifact.encrypted_value != ""
        decrypted = crypto.decrypt_secret(persisted_artifact.encrypted_value)
        assert decrypted == "SecurePass-999!"


@pytest.mark.asyncio
async def test_deliver_api_endpoint():
    token = secrets.token_urlsafe(32)
    async with AsyncSessionLocal() as db:
        await _ensure_user_created_policy(db)
        _p, cred, secret = await create_service_credential(
            db,
            subject="operator-delivery-service",
            display_name="Delivery Service",
            scopes={"command:create", "command:read"},
        )
        headers = {
            "X-Service-Key-Id": cred.key_id,
            "X-Service-Secret": secret,
        }

        command = CommandRecord(
            idempotency_key=f"delivery-test-api-{uuid.uuid4()}",
            request_hash="f" * 64,
            action="create_user",
            executor="windows",
            target_json={"task_id": 140005},
            params_json={"surname": "Иванов"},
            status="succeeded",
            result_json={
                "verified": True,
                "sam_account_name": "ivanov.i.i",
            },
            task_id=140005,
            initiator="operator",
            source="test",
            lease_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        db.add(command)
        await db.commit()
        await db.refresh(command)

        command.status = "running"
        await db.commit()
        await CommandSecretService(db).store(
            command.id,
            claim_token=token,
            name="temporary_password",
            value="ApiPass-12345!",
            ttl_seconds=300,
        )
        command.status = "succeeded"
        await db.commit()
        cmd_id = command.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with (
            patch(
                "app.services.command_delivery.get_service_auth_b64",
                new=AsyncMock(return_value="mock-service-auth"),
            ),
            patch(
                "app.services.intraservice.get_single_task",
                new=AsyncMock(return_value={"Id": 140005, "StatusId": 27}),
            ),
            patch(
                "app.services.intraservice.update_task_full",
                new=AsyncMock(return_value=True),
            ) as mock_update,
            patch(
                "app.services.intraservice.add_task_expenses",
                new=AsyncMock(return_value=True),
            ),
        ):
            resp = await client.post(f"/api/v2/commands/{cmd_id}/deliver", headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "delivered"
            assert data["target_status_id"] == 29
            assert "ApiPass-12345!" not in str(data)
            mock_update.assert_awaited_once()
