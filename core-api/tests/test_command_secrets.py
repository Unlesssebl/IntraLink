import hashlib
import secrets

import pytest
from cryptography.fernet import Fernet

from app.database.db import AsyncSessionLocal, CommandRecord
from app.services import crypto
from app.services.command_secrets import CommandSecretService
from app.services.decision_journal import sanitize_payload


@pytest.mark.asyncio
async def test_secret_artifact_is_encrypted_and_one_time(monkeypatch):
    monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))
    token = secrets.token_urlsafe(32)
    async with AsyncSessionLocal() as db:
        command = CommandRecord(
            idempotency_key="secret-artifact-test",
            request_hash="a" * 64,
            action="create_user",
            executor="windows",
            target_json={"task_id": 1},
            params_json={},
            status="running",
            initiator="test",
            source="test",
            lease_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        db.add(command)
        await db.commit()
        await db.refresh(command)

        service = CommandSecretService(db)
        artifact = await service.store(
            command.id,
            claim_token=token,
            name="temporary_password",
            value="Temp-Password-123",
            ttl_seconds=300,
        )
        assert "Temp-Password-123" not in artifact.encrypted_value

        consumed, value = await service.consume(command.id, name="temporary_password")
        assert value == "Temp-Password-123"
        assert consumed.encrypted_value == ""
        with pytest.raises(Exception) as exc:
            await service.consume(command.id, name="temporary_password")
        assert getattr(exc.value, "status_code", None) == 410


def test_command_payload_sanitizer_never_persists_password_values():
    payload = sanitize_payload(
        {"payload": {"temporary_password": "Secret-123"}, "message": "password: Secret-123"}
    )
    assert "Secret-123" not in str(payload)
